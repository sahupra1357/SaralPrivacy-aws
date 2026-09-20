import { defineConfig } from "@hey-api/openapi-ts";

export default defineConfig({
  input: "./openapi.json",
  output: { path: "./src/client", format: "prettier" },
  plugins: ["@hey-api/client-fetch", { name: "@hey-api/sdk", asClass: true }],
});
