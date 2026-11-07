// Names for the generated API types (src/api/schema.d.ts, from the server's Pydantic models).
import type { components } from "./schema";

type Schemas = components["schemas"];
export type SessionSummary = Schemas["SessionSummary"];
export type SessionDetail = Schemas["SessionDetail"];
export type ChatEntry = Schemas["ChatEntry"];
export type Utterance = Schemas["Utterance"];
export type AgentSummary = Schemas["AgentSummary"];
export type LoggedIn = Schemas["LoggedIn"];
export type Me = Schemas["Me"];
export type MessageAccepted = Schemas["MessageAccepted"];
