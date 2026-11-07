// Names for the generated API types (src/api/schema.d.ts, from the server's Pydantic models).
import type { components } from "./schema";

type Schemas = components["schemas"];
export type SessionSummary = Schemas["SessionSummary"];
export type SessionDetail = Schemas["SessionDetail"];
export type SessionConfig = Schemas["SessionConfig"];
export type ChatEntry = Schemas["ChatEntry"];
export type Utterance = Schemas["Utterance"];
export type UploadedImage = Schemas["UploadedImage"];
export type CropBox = Schemas["CropBox"];
export type DictationAccepted = Schemas["DictationAccepted"];
export type DictatedTextResult = Schemas["DictatedTextResult"];
export type Settings = Schemas["SettingsResponse"];
export type DocumentFolder = Schemas["DocumentFolder"];
export type DocumentRow = Schemas["DocumentRow"];
export type DocumentDeleted = Schemas["DocumentsDeleted"];
export type DocumentPreview = Schemas["DocumentPreview"];
export type SettingsUpdate = Schemas["SettingsUpdate"];
export type AgentSummary = Schemas["AgentSummary"];
export type QuickChoices = Schemas["QuickChoices"];
export type AgentChoices = Schemas["AgentChoices"];
export type ModelOption = Schemas["ModelOption"];
export type ChoicesChange = Schemas["ChoicesChange"];
export type LoggedIn = Schemas["LoggedIn"];
export type Me = Schemas["Me"];
export type MessageAccepted = Schemas["MessageAccepted"];
