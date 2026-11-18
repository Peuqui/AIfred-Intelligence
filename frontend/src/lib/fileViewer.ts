// The viewer: ONE decision for how the UI shows a file's content, for every place that has files (the local documents
// folder, a document source, a file area of the requester). Every place gives the address of the file as the browser
// shows it (``viewUrl``: the server sends a page sandboxed, a PDF, audio or video inline with its type). A page, a PDF,
// audio and video open in a tab of their own (the browser's own page, player and PDF viewer: the same on a phone); an
// image shows in the dialog; any other file shows as text, as the index's parser reads it or as raw text (the server's
// preview, one shape for every place).
import { ref } from "vue";
import type { DocumentPreview } from "../api/types";

const PAGE_FILE = /\.html?$/i;
const IMAGE_FILE = /\.(png|jpe?g|gif|webp|svg|bmp)$/i;
const MEDIA_FILE = /\.(wav|mp3|ogg|flac|m4a|mp4|webm|mov|pdf)$/i;

export type FileKind = "page" | "image" | "media" | "text";   // media: PDF, audio, video

export function fileKind(name: string): FileKind {
  if (PAGE_FILE.test(name)) return "page";
  if (IMAGE_FILE.test(name)) return "image";
  return MEDIA_FILE.test(name) ? "media" : "text";
}

export const isPageFile = (name: string): boolean => fileKind(name) === "page";
export const opensInTab = (name: string): boolean => ["page", "media"].includes(fileKind(name));

// What the dialog shows: a file's text, or its image.
export type Viewed = { name: string; text: string; imageUrl?: undefined } | { name: string; imageUrl: string; text?: undefined };

export const viewed = ref<Viewed | null>(null);

// A place describes a file: its name, its view address and how the server's text preview is asked. A failed text
// preview (null) leaves the place to show why.
export interface ViewPlace {
  name: string;
  viewUrl: string;
  text: () => Promise<DocumentPreview | null>;
}

export async function viewFile(place: ViewPlace): Promise<void> {
  const kind = fileKind(place.name);
  if (kind === "page" || kind === "media") {
    window.open(place.viewUrl, "_blank", "noopener");
  } else if (kind === "image") {
    viewed.value = { name: place.name, imageUrl: place.viewUrl };
  } else {
    const preview = await place.text();
    if (preview) viewed.value = preview;
  }
}
