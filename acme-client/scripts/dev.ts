const bunBinary = Bun.which("bun") ?? process.execPath;

const serverProc = Bun.spawn({
  cmd: [bunBinary, "run", "dev:server"],
  stdout: "inherit",
  stderr: "inherit",
  stdin: "inherit",
});

const webProc = Bun.spawn({
  cmd: [bunBinary, "run", "dev:web"],
  stdout: "inherit",
  stderr: "inherit",
  stdin: "inherit",
});

const shutdown = () => {
  serverProc.kill();
  webProc.kill();
};

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);

const exitCode = await Promise.race([
  serverProc.exited.then((code) => ({ who: "server", code })),
  webProc.exited.then((code) => ({ who: "web", code })),
]);

shutdown();

if (exitCode.code !== 0) {
  console.error(`${exitCode.who} process exited with code ${exitCode.code}`);
}

process.exit(exitCode.code);

export {};
