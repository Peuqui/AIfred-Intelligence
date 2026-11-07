import "katex/dist/katex.min.css";
import "highlight.js/styles/github-dark.css";
import { createApp } from "vue";
import { createPinia } from "pinia";
import App from "./App.vue";
import { router } from "./router";
import { apiUrl } from "./api/client";

// Colours come from the server (aifred/theme.py, COLORS) as CSS variables.
const theme = document.createElement("link");
theme.rel = "stylesheet";
theme.href = apiUrl("app/theme.css");
document.head.append(theme);

createApp(App).use(createPinia()).use(router).mount("#app");
