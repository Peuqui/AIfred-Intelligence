#!/usr/bin/env python3
"""
Apply Dark Anthrazit Theme and Layout Improvements to AIfred Reflex UI
"""

with open('aifred/aifred.py', 'r') as f:
    content = f.read()

# 1. Add theme import
content = content.replace(
    'import reflex as rx\nfrom .state import AIState',
    'import reflex as rx\nfrom .state import AIState\nfrom .theme import COLORS'
)

# 2. Move chat history to bottom (remove from right_column)
content = content.replace(
    '''def right_column() -> rx.Component:
    """Complete right column with output displays"""
    return rx.vstack(
        chat_display(),
        tts_section(),
        chat_history_display(),
        spacing="4",
        width="100%",
    )''',
    '''def right_column() -> rx.Component:
    """Complete right column with output displays"""
    return rx.vstack(
        chat_display(),
        tts_section(),
        # Chat history moved to bottom (full width)
        spacing="4",
        width="100%",
    )'''
)

# 3. Add chat history after 2-column layout
content = content.replace(
    '''            ),

            # Debug Console (bottom)
            debug_console(),''',
    '''            ),

            # Chat History (full width below columns)
            chat_history_display(),

            # Debug Console (bottom)
            debug_console(),'''
)

# 4. Increase max-width
content = content.replace(
    'max_width="1600px"',
    'max_width="2000px"  # Increased for better screen utilization'
)

# 5. WhatsApp-style layout (User rechts, AI links)
content = content.replace(
    '''                    # User message
                    rx.hstack(
                        rx.text("👤", font_size="20px"),
                        rx.box(
                            rx.text(msg[0], color="white"),
                            background_color="#2563eb",
                            padding="3",
                            border_radius="8px",
                            max_width="80%",
                        ),
                        spacing="3",
                        align="start",
                        justify="start",
                        width="100%",
                    ),
                    # AI message
                    rx.hstack(
                        rx.text("🤖", font_size="20px"),
                        rx.box(
                            rx.markdown(msg[1]),
                            background_color="#e5e7eb",
                            padding="3",
                            border_radius="8px",
                            max_width="80%",
                        ),
                        spacing="3",
                        align="start",
                        justify="start",
                        width="100%",
                    ),''',
    '''                    # User message (rechts, wie WhatsApp)
                    rx.hstack(
                        rx.spacer(),
                        rx.box(
                            rx.text(msg[0], color=COLORS["user_text"]),
                            background_color=COLORS["user_msg"],
                            padding="3",
                            border_radius="6px",
                            max_width="70%",
                        ),
                        rx.text("👤", font_size="16px"),
                        spacing="2",
                        align="start",
                        justify="end",
                        width="100%",
                    ),
                    # AI message (links, wie WhatsApp)
                    rx.hstack(
                        rx.text("🤖", font_size="16px"),
                        rx.box(
                            rx.markdown(msg[1]),
                            background_color=COLORS["ai_msg"],
                            padding="3",
                            border_radius="6px",
                            max_width="70%",
                        ),
                        rx.spacer(),
                        spacing="2",
                        align="start",
                        justify="start",
                        width="100%",
                    ),'''
)

# 6. Apply dark theme colors
content = content.replace('background_color="#f3f4f6"', 'background_color=COLORS["page_bg"]')
content = content.replace('background_color="white"', 'background_color=COLORS["card_bg"]')
content = content.replace('background_color="#f9fafb"', 'background_color=COLORS["readonly_bg"]')
content = content.replace('border="1px solid #e5e7eb"', 'border=f"1px solid {COLORS[\'border\']}"')
content = content.replace('color_scheme="blue"', 'color_scheme="orange"')

# 7. Make accordion headers smaller
content = content.replace(
    'rx.text("🐛 Debug Console", font_weight="bold"',
    'rx.text("🐛 Debug Console", font_size="13px", font_weight="500"'
)
content = content.replace(
    'rx.text("⚙️ Einstellungen", font_weight="bold"',
    'rx.text("⚙️ Einstellungen", font_size="13px", font_weight="500"'
)

# Write back
with open('aifred/aifred.py', 'w') as f:
    f.write(content)

print("✅ Dark theme and layout improvements applied!")
print("✅ User messages now on the right (like WhatsApp)")
print("✅ Chat history moved to full width below columns")
print("✅ Max-width increased to 2000px")
print("✅ Anthrazit color scheme applied")
