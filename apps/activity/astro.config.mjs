import { defineConfig } from "astro/config";

export default defineConfig({
  output: "static",
  vite: {
    server: {
      allowedHosts: ["jukebotx.cortocast.com"],
    },
  },
});
