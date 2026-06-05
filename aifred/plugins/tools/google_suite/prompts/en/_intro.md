You have access to Google Calendar. Use google_calendar_list_events to query events, google_calendar_create_event to create, google_calendar_update_event to modify, google_calendar_delete_event to delete. Timestamps must be RFC 3339 (e.g. 2026-04-22T10:00:00+02:00).

You have access to Google Contacts. Use google_contacts_search to find contacts by name or email.

You have access to Google Tasks. Use google_tasks_list to retrieve tasks, google_tasks_create to create new ones, google_tasks_complete to mark them done, google_tasks_delete to remove them. Use google_tasks_list_tasklists to see all task lists.

You have access to Google Drive. Use google_drive_list_files to list files (optionally filter by folder_id), google_drive_search for full-text search, google_drive_get_file to read file content (Google Docs → plain text, Sheets → CSV), google_drive_create_file to create new text files, google_drive_update_file to overwrite content, google_drive_delete_file to delete, google_drive_create_folder to create folders, and google_drive_move_file to move files.
