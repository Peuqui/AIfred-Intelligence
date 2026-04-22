# EPIM Plugin

**File:** `aifred/plugins/tools/epim/`

CRUD operations on EssentialPIM Firebird database. Allows the LLM to manage contacts, appointments, notes, tasks, passwords and other entities.

## Tools

| Tool | Description | Tier |
|------|------------|------|
| `epim_search` | Search entries (contacts, appointments, notes, tasks, passwords, categories, calendars, todo lists, note trees) | READONLY |
| `epim_create` | Create new entry | WRITE_DATA |
| `epim_update` | Update existing entry | WRITE_DATA |
| `epim_delete` | Delete entry | WRITE_SYSTEM |

## Features

- **Name-to-ID resolution:** Natural references like "meeting with Max" are automatically resolved to the correct DB ID
- **7-day date reference:** Relative date expressions ("tomorrow", "next Monday") are correctly interpreted
- **Anti-hallucination:** Strict validation prevents fabricated IDs or fields
- **Field mapping:** Internal DB field names are mapped to user-friendly names
