import { defineConfig } from "@hey-api/openapi-ts";

// The input is the committed document, read where it lives. The spike
// never regenerates it and never edits it: it is the seam under test.
export default defineConfig({
  input: "../../../docs/reference/api-openapi.json",
  output: {
    path: "./generated",
  },
  plugins: [
    "@hey-api/client-fetch",
    "@hey-api/typescript",
    "@hey-api/sdk",
  ],
});
