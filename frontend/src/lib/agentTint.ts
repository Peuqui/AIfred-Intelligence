// A hue of its own for every agent, from its id: the same agent always gets the same, a new one gets its own
// without a list to keep. The bubble mixes only a few percent of it into card_bg (theme.py: agent_tint_mix).
export function agentHue(agent: string): number {
  let hash = 0;
  for (const char of agent) hash = (hash * 31 + char.codePointAt(0)!) % 360;
  return hash;
}
