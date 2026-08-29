import { defineConfig } from "vite";
import tailwindcss from "@tailwindcss/vite";

// `base` must match Django's STATIC_URL + DJANGO_VITE static_url_prefix. django-vite
// builds both the dev-server URL and the production URL from that same pair, so the two
// only ever agree if this string does.
export default defineConfig({
    base: "/static/dist/",
    plugins: [tailwindcss()],
    build: {
        outDir: "static/dist",
        emptyOutDir: true,
        manifest: "manifest.json",
        rollupOptions: {
            input: "frontend/main.js",
        },
    },
    server: {
        port: 5173,
        strictPort: true,
        // Django serves the page from :8000 and loads modules from :5173.
        cors: true,
    },
});
