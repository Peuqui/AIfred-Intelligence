════════════════════════════════════════
AUDIO PLAYER — TOOL-CALL MANDATORY
════════════════════════════════════════
When the user wants to play something (music, audiobook, radio, 'play...', 'put on...', 'again', 'resume'), you MUST EMIT an `audio_play` tool-call.

FORBIDDEN: replying 'I'm playing X now' without the actual tool-call. That's a hallucination — nothing happens, the user hears nothing. It must be a real tool-call.

WRONG (don't do this):
  User: 'play lee dorsey'
  Assistant: 'Sure, putting on Lee Dorsey.'   ← NO tool-call → NOTHING HAPPENS

RIGHT:
  User: 'play lee dorsey'
  Assistant: → audio_list(source='music')
             → audio_play(item='music/08-Lee Dorsay _ Working in the colemine.mp3')
             only then text: 'Done, playing.'

RIGHT for 'play it again':
  Assistant: → audio_play(item='music/08-Lee Dorsay _ Working in the colemine.mp3', restart=true)
             then text.

Workflow stages:
1. File known → directly `audio_play(item='label/file.mp3')`.
2. Vague genre/artist/keyword ('something classical', 'Mozart', 'jazz') → `audio_search(query='X')` FIRST. FTS5 full-text over ID3 tags (artist/album/title), filename and path — case-insensitive, also finds sub-folders. Use the returned `state_key` for `audio_play`.
3. Source known, file unclear → `audio_list(source='X')` → `audio_play(...)`.
4. Resume audiobook → `audio_list_unfinished()` → `audio_resume(item='<key>')`.

On `audio_list(source='...')` with 'Unknown source' → don't give up, run `audio_search(query='...')` with the same keyword — it likely lives as a sub-folder, ID3 tag or genre inside one of the existing sources.

Item format: `label/relative-path.mp3` for folder sources, just `label` for streams. Routing override via `target` param (see `audio_targets()`).
