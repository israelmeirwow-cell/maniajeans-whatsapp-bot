// Cloudflare Worker that routes all traffic into the bot container (single instance —
// the bot keeps sessions in memory, so every request must reach the same container).
import { Container } from "@cloudflare/containers";

export class BotContainer extends Container {
  defaultPort = 8080;
  sleepAfter = "2h";        // idle window before the container stops (cold start ~2-3s)
}

export default {
  async fetch(request, env) {
    const container = env.BOT.getByName("main");
    await container.startAndWaitForPorts();
    return container.fetch(request);
  },
};
