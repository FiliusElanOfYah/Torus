#!/usr/bin/env node
// The preview platform launches `yarn expo start --port 3000`. This web guide is a Create React App
// (craco) project, not an Expo app, so the `expo` script points here: ignore Expo's arguments and run
// the CRA dev server on port 3000 bound to all interfaces, which is what the preview proxy expects.
const { spawn } = require("child_process");

const env = {
  ...process.env,
  PORT: "3000",
  HOST: "0.0.0.0",
  BROWSER: "none",
  DANGEROUSLY_DISABLE_HOST_CHECK: "true",
  WDS_SOCKET_PORT: process.env.WDS_SOCKET_PORT || "443",
};
const child = spawn("node", [require.resolve("@craco/craco/dist/bin/craco.js"), "start"], { stdio: "inherit", env });
for (const sig of ["SIGTERM", "SIGINT"]) process.on(sig, () => child.kill(sig));
child.on("exit", (code) => process.exit(code ?? 0));
