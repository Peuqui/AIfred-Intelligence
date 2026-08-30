"""
Modell-Analyse fuer die vLLM-Kalibration (Phase A).

Liest config.json und die Safetensors-Header eines Checkpoints und
liefert die Fakten, aus denen die Suche ihre Kandidaten baut:

- exakte Gewichts-Bytes gesamt, je Layer und je Komponente
  (aus den data_offsets der Safetensors-Header — keine dtype-Schaetzung)
- Layer-gebundene Riesen-Komponenten (PLE-Klasse: ein Layer traegt ein
  Vielfaches der anderen → bestimmt die PP-Partition und den Offload)
- MTP-Draft-Block: vorhanden? quantisiert? (BF16-Draftkopf macht
  Spekulation auf langsamem VRAM zum Verlustgeschaeft — dann k=0)
- QSA-Blockgroessen-Arithmetik: erlaubte k und zugehoerige block_size
- nativer Kontext, Multimodalitaet (--language-model-only)
"""

import json
import math
import struct
from dataclasses import dataclass, field
from pathlib import Path

# Layer, dessen Gewichte das X-fache des Medians wiegen, gilt als
# "gebundener Riese" (Flash-Next: PLE-Layer 52,7 GB gegen 1,7-1,9 GB).
LAYER_GIANT_FACTOR = 4.0


@dataclass
class MtpInfo:
    present: bool
    bytes_total: int = 0
    expert_bytes: int = 0         # geroutete Experten IM Draft-Block (MoE-MTP)
    quantized: bool = False       # Skalen-Tensoren unter mtp.* vorhanden?
    dominant_dtype: str = ""


@dataclass
class VllmModelMeta:
    checkpoint: Path
    architecture: str
    num_layers: int
    native_context: int
    total_bytes: int
    layer_bytes: dict[int, int]           # Layer-Index -> Bytes
    component_bytes: dict[str, int]       # Top-Level-Komponente -> Bytes
    giant_layers: list[int]               # Layer mit gebundenen Riesen
    mtp: MtpInfo = field(default_factory=lambda: MtpInfo(False))
    compress_ratio: int = 1               # QSA-Indexer (1 = kein QSA)
    multimodal: bool = False
    expert_bytes: int = 0                 # geroutete Experten (".experts.N.")
    ple_bytes: int = 0                    # Hash-N-Gram-Tabellen (".ple.")
    num_experts: int = 0
    experts_per_tok: int = 0

    def per_token_read_bytes(self) -> int:
        """Geschaetzte Gewichts-Bytes, die ein Decode-Token liest.

        Dense: alles ausser Vision/MTP. MoE: geroutete Experten nur
        anteilig (aktive/gesamt); PLE-Tabellen praktisch gar nicht
        (Hash-Lookup, ~KB/Token). Grundlage fuer den Lohnt-sich-Check
        der Spekulation (MTP-Block relativ zur Leselast).
        """
        visual = sum(b for c, b in self.component_bytes.items() if "visual" in c)
        base = self.total_bytes - visual - self.mtp.bytes_total \
            - self.expert_bytes - self.ple_bytes
        if self.num_experts > 0 and self.experts_per_tok > 0:
            base += int(self.expert_bytes * self.experts_per_tok / self.num_experts)
        else:
            base += self.expert_bytes
        return base

    def mtp_read_bytes_per_step(self) -> int:
        """Leselast EINES Draft-Schritts: ein MoE-MTP-Block liest seine
        gerouteten Experten ebenfalls nur anteilig (Flash-Next-MTPQ: 512
        Einzelexperten, 10 aktiv) — der Roh-Blockwert wuerde Spekulation
        faelschlich als unrentabel einstufen."""
        if not self.mtp.present:
            return 0
        rest = self.mtp.bytes_total - self.mtp.expert_bytes
        if self.num_experts > 0 and self.experts_per_tok > 0:
            return rest + int(
                self.mtp.expert_bytes * self.experts_per_tok / self.num_experts
            )
        return self.mtp.bytes_total

    def allowed_k_block_sizes(self, k_max: int = 8) -> dict[int, int]:
        """Erlaubte Spekulationstiefen mit kleinster passender block_size.

        Die QSA-Ringkapazitaet muss die Attention-Blockgroesse teilen:
        capacity = ratio * ceil((ratio + k) / ratio). Grosse Kapazitaeten
        erzwingen grosse Bloecke (Flash-Next: k=5-8 -> block 48 -> vom
        Ring gesperrt, weil unwirtschaftlich); zurueckgegeben wird die
        Arithmetik, die Bewertung trifft die Suche.
        """
        result: dict[int, int] = {0: 16}
        if self.compress_ratio <= 1:
            for k in range(1, k_max + 1):
                result[k] = 16
            return result
        ratio = self.compress_ratio
        for k in range(1, k_max + 1):
            capacity = ratio * math.ceil((ratio + k) / ratio)
            result[k] = math.lcm(16, capacity)
        return result

    def boot_block_size(self, k: int) -> int:
        """Blockgroesse fuer den Boot: die kleinste ring-gueltige.

        Groessere Bloecke sind KEIN universeller Gewinn: Block 32 misst
        am 27B +0,6 % Prefill, am Flash-Next-Hybrid (QSA/GDN) aber
        -12 % (2026-08-30). Die Achse ist modellspezifisch — wer sie
        heben will, muss sie pro Modell messen.
        """
        return self.allowed_k_block_sizes()[k]


def _read_safetensors_header(path: Path) -> dict:
    """Nur den JSON-Header lesen (8-Byte-Laenge + Header), keine Gewichte."""
    with open(path, "rb") as f:
        (header_len,) = struct.unpack("<Q", f.read(8))
        header: dict = json.loads(f.read(header_len))
        return header


def _tensor_bytes(entry: dict) -> int:
    begin, end = entry["data_offsets"]
    return int(end) - int(begin)


def _layer_index(tensor_name: str) -> int | None:
    """Layer-Index aus Namen wie 'model.layers.17.mlp...' (None sonst)."""
    parts = tensor_name.split(".")
    for i, part in enumerate(parts[:-1]):
        if part == "layers" and parts[i + 1].isdigit():
            return int(parts[i + 1])
    return None


def _component(tensor_name: str) -> str:
    """Grobe Top-Level-Komponente (mtp, model, visual, lm_head, ...)."""
    first = tensor_name.split(".", 1)[0]
    if first == "model" and "." in tensor_name:
        second = tensor_name.split(".")[1]
        if second != "layers":
            return f"model.{second}"
    return first


def analyze_checkpoint(checkpoint: Path) -> VllmModelMeta:
    """Checkpoint-Verzeichnis analysieren (config.json + Safetensors-Header)."""
    config = json.loads((checkpoint / "config.json").read_text())
    text = config.get("text_config", config)

    architecture = (config.get("architectures") or ["unknown"])[0]
    num_layers = int(text.get("num_hidden_layers", 0))
    native_context = int(
        text.get("max_position_embeddings")
        or text.get("max_seq_len")
        or 0
    )
    compress_ratio = int(text.get("indexer_compress_ratio", 1) or 1)
    multimodal = "vision_config" in config or "video_preprocessor" in str(
        sorted(p.name for p in checkpoint.glob("*preprocessor*"))
    )

    # Tensor-Landkarte: Index-Datei nennt die Shards, die Header die Bytes
    index_path = checkpoint / "model.safetensors.index.json"
    if index_path.exists():
        weight_map: dict[str, str] = json.loads(index_path.read_text())["weight_map"]
        shard_files = sorted(set(weight_map.values()))
    else:
        shard_files = sorted(p.name for p in checkpoint.glob("*.safetensors"))
        if not shard_files:
            raise FileNotFoundError(f"no safetensors in {checkpoint}")

    total = 0
    layer_bytes: dict[int, int] = {}
    component_bytes: dict[str, int] = {}
    expert_bytes = 0
    ple_bytes = 0
    mtp_bytes = 0
    mtp_expert_bytes = 0
    mtp_dtypes: dict[str, int] = {}
    mtp_has_scales = False

    for shard in shard_files:
        header = _read_safetensors_header(checkpoint / shard)
        for name, entry in header.items():
            if name == "__metadata__":
                continue
            nbytes = _tensor_bytes(entry)
            total += nbytes
            comp = _component(name)
            component_bytes[comp] = component_bytes.get(comp, 0) + nbytes
            layer = _layer_index(name)
            if layer is not None:
                layer_bytes[layer] = layer_bytes.get(layer, 0) + nbytes
            if ".experts." in name:
                expert_bytes += nbytes
            if ".ple." in name:
                ple_bytes += nbytes
            if comp in ("mtp", "model.mtp"):
                mtp_bytes += nbytes
                if ".experts." in name:
                    mtp_expert_bytes += nbytes
                dtype = entry.get("dtype", "?")
                mtp_dtypes[dtype] = mtp_dtypes.get(dtype, 0) + nbytes
                if "scale" in name:
                    mtp_has_scales = True

    # Gebundene Riesen: Layer weit ueber dem Median
    giant_layers: list[int] = []
    if layer_bytes:
        sizes = sorted(layer_bytes.values())
        median = sizes[len(sizes) // 2]
        giant_layers = [
            idx for idx, b in sorted(layer_bytes.items())
            if median > 0 and b > LAYER_GIANT_FACTOR * median
        ]

    mtp = MtpInfo(present=mtp_bytes > 0)
    if mtp.present:
        mtp.bytes_total = mtp_bytes
        mtp.expert_bytes = mtp_expert_bytes
        mtp.quantized = mtp_has_scales
        mtp.dominant_dtype = max(mtp_dtypes, key=lambda d: mtp_dtypes[d])

    return VllmModelMeta(
        checkpoint=checkpoint,
        architecture=architecture,
        num_layers=num_layers,
        native_context=native_context,
        total_bytes=total,
        layer_bytes=layer_bytes,
        component_bytes=component_bytes,
        giant_layers=giant_layers,
        mtp=mtp,
        compress_ratio=compress_ratio,
        multimodal=multimodal,
        expert_bytes=expert_bytes,
        ple_bytes=ple_bytes,
        num_experts=int(text.get("num_experts", 0) or 0),
        experts_per_tok=int(text.get("num_experts_per_tok", 0) or 0),
    )
