// The viewer: ONE decision for how the UI shows a file's content, for every place that has files (the local documents
// folder, a document source, a file area of the requester). Every place gives the address of the file as the browser
// shows it (``viewUrl``: the server sends a page sandboxed, a PDF, audio or video inline with its type). FilePreview
// shows it by its kind, embedded (a page and a PDF in a frame, an image, a player) or as text, as the index's parser
// reads it or as raw text (the server's preview, one shape for every place). ``viewFile`` shows it in the dialog
// (FileViewer); the documents page shows the same FilePreview beside its list.
import { ref } from "vue";
import { api, apiUrl } from "../api/client";
import type { DocumentPreview } from "../api/types";

const PAGE_FILE = /\.html?$/i;
const IMAGE_FILE = /\.(png|jpe?g|gif|webp|svg|bmp)$/i;
const AUDIO_FILE = /\.(wav|mp3|ogg|flac|m4a)$/i;
const VIDEO_FILE = /\.(mp4|webm|mov)$/i;
const PDF_FILE = /\.pdf$/i;

export type FileKind = "page" | "image" | "pdf" | "audio" | "video" | "text";

export function fileKind(name: string): FileKind {
  if (PAGE_FILE.test(name)) return "page";
  if (IMAGE_FILE.test(name)) return "image";
  if (PDF_FILE.test(name)) return "pdf";
  if (AUDIO_FILE.test(name)) return "audio";
  if (VIDEO_FILE.test(name)) return "video";
  return "text";
}

export const isPageFile = (name: string): boolean => fileKind(name) === "page";

// A place describes a file: its name, its view address and how the server's text preview is asked (only for the kinds
// shown as text). A failed text preview rejects: FilePreview shows the reason.
export interface ViewPlace {
  name: string;
  viewUrl: string;
  text?: () => Promise<DocumentPreview>;
}

// The file of a file area (lib/file_areas.py, /files/{area}): the requester and the documents page both take it from here.
export function areaPlace(area: string, entry: { basename: string; path: string }): ViewPlace {
  const query = `path=${encodeURIComponent(entry.path)}`;
  return {
    name: entry.basename,
    viewUrl: apiUrl(`files/${area}/preview?${query}`),
    text: () => api<DocumentPreview>(`files/${area}/text?${query}`),
  };
}

// What the dialog shows (mounted once in the shell).
export const viewed = ref<ViewPlace | null>(null);

export function viewFile(place: ViewPlace): void {
  viewed.value = place;
}
