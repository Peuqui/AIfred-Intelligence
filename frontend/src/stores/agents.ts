// The agents page: every agent a chat can address, switched on or off, and what may be deleted. Switching,
// deleting and creating answer with the cards again; the quick choices (models, persona, reasoning …) of the
// agents switched on come from the agentChoices store, the same rows the quick-choice modal shows. The editor
// reads and writes an agent's persona, prompt texts and rights directly (no copy kept here). Below them the system
// agents and the Automatik LLM: their prompts through the same calls, the calibration's cloud model.
import { defineStore } from "pinia";
import { ref } from "vue";
import { ApiError, api } from "../api/client";
import type {
  AgentCard, AgentPersona, AgentRights, CloudChange, CloudModels, NewAgent, PersonaChange, PromptText, SystemAgentCard,
} from "../api/types";
import { onResourceChanged } from "../lib/resourceChanges";
import { useAgentChoices } from "./agentChoices";

export const useAgents = defineStore("agents", () => {
  const cards = ref<AgentCard[]>([]);
  const system = ref<SystemAgentCard[]>([]);
  const notice = ref("");
  const busy = ref(false);
  const choices = useAgentChoices();

  async function call(work: () => Promise<unknown>): Promise<void> {
    busy.value = true;
    notice.value = "";
    try {
      await work();
      cards.value = await api<AgentCard[]>("agents");
    } catch (error) {
      notice.value = error instanceof ApiError ? error.detail : String(error);
    } finally {
      busy.value = false;
    }
  }

  async function load(): Promise<void> {
    await Promise.all([call(async () => (system.value = await api<SystemAgentCard[]>("agents/system"))), choices.load()]);
  }

  // A switched agent leaves (or joins) the quick choices: they are loaded again with the cards.
  async function switchAgent(id: string, enabled: boolean): Promise<void> {
    await call(() => api<AgentCard>(`agents/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify({ enabled }) }));
    await choices.load();
  }

  async function remove(id: string): Promise<void> {
    await call(() => api<void>(`agents/${encodeURIComponent(id)}`, { method: "DELETE" }));
    await choices.load();
  }

  async function create(agent: NewAgent): Promise<boolean> {
    let created = false;
    await call(async () => {
      await api<AgentCard>("agents", { method: "POST", body: JSON.stringify(agent) });
      created = true;
    });
    await choices.load();
    return created;
  }

  // The editor's calls: their errors go to the editor, which shows them.
  const path = (id: string): string => `agents/${encodeURIComponent(id)}`;
  const persona = (id: string): Promise<AgentPersona> => api<AgentPersona>(`${path(id)}/persona`);
  const prompt = (id: string, lang: string, key: string): Promise<PromptText> => api<PromptText>(`${path(id)}/prompts/${lang}/${key}`);
  const rights = (id: string): Promise<AgentRights> => api<AgentRights>(`${path(id)}/tools`);

  async function changePersona(id: string, change: PersonaChange): Promise<AgentPersona> {
    const changed = await api<AgentPersona>(`${path(id)}/persona`, { method: "PATCH", body: JSON.stringify(change) });
    cards.value = await api<AgentCard[]>("agents");
    await choices.load();
    return changed;
  }

  const writePrompt = (id: string, lang: string, key: string, text: string): Promise<PromptText> =>
    api<PromptText>(`${path(id)}/prompts/${lang}/${key}`, { method: "PUT", body: JSON.stringify({ text }) });
  const changeRights = (id: string, allowed: string[] | null): Promise<AgentRights> =>
    api<AgentRights>(`${path(id)}/tools`, { method: "PUT", body: JSON.stringify({ allowed }) });

  const cloudModels = (id: string, provider: string): Promise<CloudModels> =>
    api<CloudModels>(`agents/system/${encodeURIComponent(id)}/cloud/models?provider=${encodeURIComponent(provider)}`);
  async function changeCloud(id: string, change: CloudChange): Promise<SystemAgentCard> {
    const changed = await api<SystemAgentCard>(`agents/system/${encodeURIComponent(id)}/cloud`, { method: "PATCH", body: JSON.stringify(change) });
    system.value = system.value.map((card) => (card.id === id ? changed : card));
    return changed;
  }

  // An agent was changed, switched, made or deleted (another tab, a tool): the cards and the choices are read anew.
  onResourceChanged("agents", () => void load());

  return {
    cards, system, cloudModels, changeCloud, notice, busy, load, switchAgent, remove, create, persona, prompt, rights, changePersona, writePrompt, changeRights,
  };
});
