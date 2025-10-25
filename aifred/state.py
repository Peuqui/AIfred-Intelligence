"""
Reflex State Management for AIfred Intelligence

Main state for chat, settings, and backend management
"""

import reflex as rx
from typing import List, Tuple
import asyncio
from .backends import BackendFactory, LLMMessage, LLMOptions, LLMResponse


class ChatMessage(rx.Base):
    """Single chat message"""
    role: str  # "user" or "assistant"
    content: str
    timestamp: str = ""


class AIState(rx.State):
    """Main application state"""

    # Chat History
    chat_history: List[Tuple[str, str]] = []  # [(user_msg, ai_msg), ...]
    current_user_input: str = ""
    current_ai_response: str = ""
    is_generating: bool = False

    # Backend Settings
    backend_type: str = "ollama"  # "ollama", "vllm"
    backend_url: str = "http://localhost:11434"  # Default Ollama URL
    selected_model: str = "qwen3:8b"
    available_models: List[str] = []

    # LLM Options
    temperature: float = 0.2
    num_ctx: int = 32768

    # Backend Status
    backend_healthy: bool = False
    backend_info: str = ""

    # Debug Console
    debug_messages: List[str] = []
    auto_refresh_enabled: bool = True

    async def on_load(self):
        """Called when page loads - initialize backend"""
        await self.initialize_backend()

    async def initialize_backend(self):
        """Initialize LLM backend"""
        try:
            # Update URL based on backend type
            if self.backend_type == "ollama":
                self.backend_url = "http://localhost:11434"
            elif self.backend_type == "vllm":
                # CHANGE THIS to Aragon's IP when vLLM is running there
                self.backend_url = "http://localhost:8000/v1"

            # Create backend
            backend = BackendFactory.create(
                self.backend_type,
                base_url=self.backend_url
            )

            # Health check
            self.backend_healthy = await backend.health_check()

            if self.backend_healthy:
                # Get available models
                self.available_models = await backend.list_models()

                # Set default model if not set
                if not self.selected_model and self.available_models:
                    self.selected_model = self.available_models[0]

                # Get backend info
                info = await backend.get_backend_info()
                self.backend_info = f"{info['backend']} - {len(self.available_models)} models available"

                self.add_debug(f"✅ {self.backend_type} backend ready: {self.backend_info}")
            else:
                self.backend_info = f"{self.backend_type} not reachable"
                self.add_debug(f"❌ {self.backend_type} backend not reachable at {self.backend_url}")

            await backend.close()

        except Exception as e:
            self.backend_healthy = False
            self.backend_info = f"Error: {str(e)}"
            self.add_debug(f"❌ Backend initialization failed: {e}")

    async def switch_backend(self, new_backend: str):
        """Switch to different backend"""
        self.add_debug(f"🔄 Switching backend from {self.backend_type} to {new_backend}...")
        self.backend_type = new_backend
        await self.initialize_backend()

    def add_debug(self, message: str):
        """Add message to debug console"""
        import datetime
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.debug_messages.append(f"{timestamp} | {message}")

        # Keep only last 100 messages
        if len(self.debug_messages) > 100:
            self.debug_messages = self.debug_messages[-100:]

    def set_user_input(self, text: str):
        """Update user input"""
        self.current_user_input = text

    async def send_message(self):
        """Send message to LLM and get response"""
        if not self.current_user_input.strip():
            return

        if self.is_generating:
            self.add_debug("⚠️ Already generating, please wait...")
            return

        user_msg = self.current_user_input.strip()
        self.current_user_input = ""  # Clear input
        self.is_generating = True
        self.current_ai_response = ""

        self.add_debug(f"📨 User: {user_msg[:50]}...")

        try:
            # Create backend
            backend = BackendFactory.create(
                self.backend_type,
                base_url=self.backend_url
            )

            # Build messages
            messages = []

            # Add chat history
            for user_turn, ai_turn in self.chat_history:
                messages.append(LLMMessage(role="user", content=user_turn))
                messages.append(LLMMessage(role="assistant", content=ai_turn))

            # Add current message
            messages.append(LLMMessage(role="user", content=user_msg))

            # LLM Options
            options = LLMOptions(
                temperature=self.temperature,
                num_ctx=self.num_ctx
            )

            self.add_debug(f"🤖 Calling {self.backend_type} ({self.selected_model})...")

            # Stream response
            full_response = ""
            async for chunk in backend.chat_stream(self.selected_model, messages, options):
                full_response += chunk
                self.current_ai_response = full_response
                yield  # Update UI in real-time

            # Add to history
            self.chat_history.append((user_msg, full_response))
            self.current_ai_response = ""

            self.add_debug(f"✅ Response complete ({len(full_response)} chars)")

            await backend.close()

        except Exception as e:
            error_msg = f"Error: {str(e)}"
            self.current_ai_response = error_msg
            self.add_debug(f"❌ Generation failed: {e}")

        finally:
            self.is_generating = False

    def clear_chat(self):
        """Clear chat history"""
        self.chat_history = []
        self.current_ai_response = ""
        self.add_debug("🗑️ Chat cleared")

    def toggle_auto_refresh(self):
        """Toggle debug console auto-refresh"""
        self.auto_refresh_enabled = not self.auto_refresh_enabled

    def set_selected_model(self, model: str):
        """Set selected model"""
        self.selected_model = model
        self.add_debug(f"📝 Model changed to: {model}")

    def set_temperature(self, temp: float):
        """Set temperature"""
        self.temperature = temp
