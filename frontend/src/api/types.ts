// Names for the generated API types (src/api/schema.d.ts, from the server's Pydantic models).
import type { components } from "./schema";

type Schemas = components["schemas"];
export type SessionSummary = Schemas["SessionSummary"];
export type SessionDetail = Schemas["SessionDetail"];
export type SessionConfig = Schemas["SessionConfig"];
export type ChatEntry = Schemas["ChatEntry"];
export type Utterance = Schemas["Utterance"];
export type UploadedImage = Schemas["UploadedImage"];
export type DictationAccepted = Schemas["DictationAccepted"];
export type DictationSettings = Schemas["DictationSettings"];
export type DictatedTextResult = Schemas["DictatedTextResult"];
export type SpeechSettings = Schemas["SpeechSettingsOut"];
export type AgentSummary = Schemas["AgentSummary"];
export type LoggedIn = Schemas["LoggedIn"];
export type Me = Schemas["Me"];
export type MessageAccepted = Schemas["MessageAccepted"];
