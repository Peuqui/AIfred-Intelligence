"""Describers on other machines: each runs its own llama-swap and its own GPU-Orc (``VISION_REMOTE_HOSTS``).

A model there is addressed as ``<machine>/<model>``, the form llama-swap gives the models of its peers;
callers hand the id on like any other, and :func:`endpoint` turns it back into the machine's llama-swap
and the model id there. The machine's ``/v1/models`` is the one list of what it has: which models see
(``capabilities.vision``), their context and whether they are loaded. AIfred keeps no list of models of
another machine; which one describes is chosen in the Vigilantia settings (``remote_describers``:
machine name → model).

Such a machine is off or asleep as a matter of course: before every choice it gets one short look
(``VISION_REMOTE_HEALTH_TIMEOUT_S``), and a machine that does not answer is passed over without a
warning (the log file says why). Its room is GPU-Orc's word there, as on this machine: a model that is
loaded fits; otherwise its measured need (stress burn-in, peak above the load before it, stored per
machine in ``vlm_vram_cache``) plus ``VLM_HEADROOM_MIB`` must be free; a model never measured does not fit.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import httpx

from .config import (
    GPU_ORC_MODEL_SERVER,
    GPU_ORC_STATUS_TIMEOUT_S,
    VISION_REMOTE_HEALTH_TIMEOUT_S,
    VISION_REMOTE_HOSTS,
    VLM_HEADROOM_MIB,
)
from .gpu_orc_client import GpuOrcClient, GpuOrcError
from .vision_vram_check import VRAMCheckResult

logger = logging.getLogger(__name__)

SETTINGS_KEY = "remote_describers"  # in the Vigilantia settings: machine name → model there
# The backend that speaks to a machine's llama-swap (chat path; the image path sends itself).
BACKEND_TYPE = "llamacpp"


class RemoteHostError(RuntimeError):
    """The machine's llama-swap did not answer or answered in a form this module does not know."""


@dataclass(frozen=True)
class RemoteHost:
    name: str
    llamaswap_url: str  # the machine's llama-swap, without /v1
    gpu_orc_url: str
    gpu_orc_token_file: str


@dataclass(frozen=True)
class RemoteModel:
    """A model of a machine that sees, as its llama-swap reports it."""

    host: RemoteHost
    model: str  # the id on the machine
    context: int  # the context its llama-swap reports (the key of its burn-in)
    loaded: bool

    @property
    def qualified(self) -> str:
        return qualified(self.host, self.model)


def remote_hosts() -> list[RemoteHost]:
    """The configured machines. A malformed ``VISION_REMOTE_HOSTS`` is an error, not an empty list."""
    try:
        entries = json.loads(VISION_REMOTE_HOSTS)
        hosts = [
            RemoteHost(
                str(e["name"]), str(e["llamaswap_url"]).rstrip("/").removesuffix("/v1"),
                str(e["gpu_orc_url"]).rstrip("/"), str(e["gpu_orc_token_file"]),
            )
            for e in entries
        ]
    except (ValueError, KeyError, TypeError) as error:
        raise ValueError(f"VISION_REMOTE_HOSTS is malformed: {error!r}") from error
    names = [h.name for h in hosts]
    if len(set(names)) != len(names) or any("/" in n or not n for n in names):
        raise ValueError(f"VISION_REMOTE_HOSTS: machine names must be unique, non-empty, without '/': {names}")
    return hosts


def qualified(host: RemoteHost, model: str) -> str:
    return f"{host.name}/{model}"


def split(model_id: str) -> tuple[RemoteHost, str] | None:
    """``(machine, model there)`` for a model of another machine, ``None`` for one of this machine."""
    name, sep, model = model_id.partition("/")
    if not sep:
        return None
    for host in remote_hosts():
        if host.name == name:
            return host, model
    return None


def endpoint(model_id: str) -> tuple[str, str] | None:
    """``(OpenAI base URL of the machine's llama-swap, model there)``, ``None`` for this machine."""
    remote = split(model_id)
    if remote is None:
        return None
    host, model = remote
    return f"{host.llamaswap_url}/v1", model


def reachable(host: RemoteHost) -> bool:
    """Does the machine's llama-swap answer right now (``/health``, short limit)?"""
    try:
        response = httpx.get(f"{host.llamaswap_url}/health", timeout=VISION_REMOTE_HEALTH_TIMEOUT_S)
    except httpx.HTTPError:
        return False
    return response.status_code == 200


def seeing_models(host: RemoteHost) -> list[RemoteModel]:
    """The machine's models that see, from its ``/v1/models``. :class:`RemoteHostError` if it does not
    answer (check :func:`reachable` first where not answering is the normal case)."""
    try:
        response = httpx.get(f"{host.llamaswap_url}/v1/models", timeout=GPU_ORC_STATUS_TIMEOUT_S)
        response.raise_for_status()
        entries = response.json()["data"]
        return [
            RemoteModel(
                host, str(e["id"]), int(e["context_length"]),
                str(e["status"]["value"]) != "unloaded",
            )
            for e in entries
            if (e.get("capabilities") or {}).get("vision") is True
        ]
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
        raise RemoteHostError(f"{host.name}: llama-swap /v1/models unreadable: {error!r}") from error


def chosen_model(host: RemoteHost) -> str:
    """The model chosen to describe on ``host`` (Vigilantia settings), ``""`` if none."""
    from .vision_settings import load_vision_settings
    return str((load_vision_settings().get(SETTINGS_KEY) or {}).get(host.name) or "")


def gpu_orc(host: RemoteHost) -> GpuOrcClient:
    """The GPU-Orc of ``host``, with its token. :class:`GpuOrcError` if the token file is missing."""
    path = Path(host.gpu_orc_token_file).expanduser()
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise GpuOrcError(f"{host.name}: GPU-Orc token file {path} unreadable: {error}") from error
    if not token:
        raise GpuOrcError(f"{host.name}: GPU-Orc token file {path} is empty")
    return GpuOrcClient(host.gpu_orc_url, token=token)


def gpu_orc_for_llamaswap(llamaswap_root: str) -> GpuOrcClient | None:
    """The GPU-Orc of the machine whose llama-swap is at ``llamaswap_root``; ``None`` for this machine."""
    root = llamaswap_root.rstrip("/").removesuffix("/v1")
    for host in remote_hosts():
        if host.llamaswap_url == root:
            return gpu_orc(host)
    return None


def check_fits(model: RemoteModel) -> VRAMCheckResult:
    """Does ``model`` fit on its machine now? Loaded fits; while a big model loads there nothing new
    goes on; otherwise measured need plus headroom against what is free (what GPU-Orc there clears
    before a load counts as free, as on this machine)."""
    from . import vlm_vram_cache

    host = model.host
    if model.loaded:
        return VRAMCheckResult(fits=True, needed_mb=0, free_mb=0, gpu_index=-1,
                               message=f"'{model.qualified}' ist bereits geladen.")
    peak_mb = vlm_vram_cache.get(model.qualified, model.context)
    if peak_mb is None:
        return VRAMCheckResult(
            fits=False, needed_mb=0, free_mb=0, gpu_index=-1,
            message=(f"'{model.qualified}' ist bei {model.context} Kontext nicht vermessen "
                     "(Burn-in fehlt) — Platzbedarf unbekannt."),
        )
    needed_mb = peak_mb + VLM_HEADROOM_MIB
    try:
        client = gpu_orc(host)
        loading = client.loading()
        cards = [] if loading else client.card_status()
    except GpuOrcError as error:
        return VRAMCheckResult(fits=False, needed_mb=needed_mb, free_mb=0, gpu_index=-1,
                               message=f"{host.name}: Belegung der Karten unbekannt — {error}")
    if loading:
        return VRAMCheckResult(fits=False, needed_mb=needed_mb, free_mb=0, gpu_index=-1,
                               message=f"{host.name}: '{', '.join(loading)}' lädt gerade.")
    free_mb = sum(
        card.total_mib - card.used_mib
        + sum(h.memory_mib or 0 for h in card.holders if h.consumer != GPU_ORC_MODEL_SERVER)
        for card in cards
    )
    if free_mb >= needed_mb:
        return VRAMCheckResult(fits=True, needed_mb=needed_mb, free_mb=free_mb, gpu_index=-1)
    return VRAMCheckResult(
        fits=False, needed_mb=needed_mb, free_mb=free_mb, gpu_index=-1,
        message=f"{host.name}: '{model.model}' braucht {needed_mb} MiB, frei sind {free_mb} MiB.",
    )


def pick() -> str | None:
    """The qualified id of the first machine's chosen describer that answers and fits now, ``None``
    when none does (each reason goes to the log file, a machine that is off is no news)."""
    for host in remote_hosts():
        wanted = chosen_model(host)
        if not wanted:
            continue
        if not reachable(host):
            logger.info("remote describer %s: %s does not answer", wanted, host.name)
            continue
        try:
            model = next((m for m in seeing_models(host) if m.model == wanted), None)
        except RemoteHostError as error:
            logger.info("remote describer %s: %s", wanted, error)
            continue
        if model is None:
            logger.info("remote describer %s: %s has no such model that sees", wanted, host.name)
            continue
        fit = check_fits(model)
        if fit.fits:
            return model.qualified
        logger.info("remote describer %s: %s", model.qualified, fit.message)
    return None


@dataclass(frozen=True)
class HostOverview:
    """What the settings show for one machine: does it answer, its models that see, the chosen one."""

    host: RemoteHost
    reachable: bool
    models: list[RemoteModel]  # empty while it does not answer
    chosen: str
    error: str  # the machine answers /health, but its model list is unreadable


def overview() -> list[HostOverview]:
    """One entry per configured machine, asked now (short limit for a machine that is off)."""
    rows = []
    for host in remote_hosts():
        up, models, error = reachable(host), [], ""
        if up:
            try:
                models = seeing_models(host)
            except RemoteHostError as exc:
                error = str(exc)
        rows.append(HostOverview(host, up, models, chosen_model(host), error))
    return rows


def save_choice(host_name: str, model: str) -> None:
    """Store the describer of ``host_name`` (``""`` = none) in the Vigilantia settings."""
    from .vision_settings import load_vision_settings, save_vision_settings
    settings = load_vision_settings()
    choices = dict(settings.get(SETTINGS_KEY) or {})
    if model:
        choices[host_name] = model
    else:
        choices.pop(host_name, None)
    settings[SETTINGS_KEY] = choices
    save_vision_settings(settings)


def chosen_remote_model(host_name: str) -> RemoteModel:
    """The chosen describer of ``host_name`` as its llama-swap reports it now (for a burn-in).
    :class:`RemoteHostError` if the machine, the choice or the model there is missing."""
    host = next((h for h in remote_hosts() if h.name == host_name), None)
    if host is None:
        raise RemoteHostError(f"no machine '{host_name}' in VISION_REMOTE_HOSTS")
    wanted = chosen_model(host)
    if not wanted:
        raise RemoteHostError(f"{host_name}: no describer chosen")
    model = next((m for m in seeing_models(host) if m.model == wanted), None)
    if model is None:
        raise RemoteHostError(f"{host_name}: '{wanted}' is not among its models that see")
    return model
