import PostalMime from "postal-mime";

interface Env {
  NEWSLETTERS: KVNamespace;
  FORWARD_TO: string;
}

const TTL_SECONDS = 7 * 24 * 60 * 60; // 7 days

export default {
  async email(message: ForwardableEmailMessage, env: Env): Promise<void> {
    // 1. Parse the MIME envelope so we have structured fields.
    let parsed: Awaited<ReturnType<PostalMime["parse"]>>;
    try {
      const buf = await new Response(message.raw).arrayBuffer();
      parsed = await new PostalMime().parse(buf);
    } catch (e) {
      console.error("MIME parse failed:", e);
      // Don't throw — we still want to try to forward to Gmail.
      parsed = { subject: "(parse failed)", text: "", html: "", date: "" } as any;
    }

    // 2. Store the parsed payload in KV so the daily digest pipeline can read it.
    const receivedAt = new Date().toISOString();
    const key = `${message.from}:${receivedAt}`;
    const payload = {
      from: message.from,
      to: message.to,
      receivedAt,
      subject: parsed.subject ?? "",
      date: parsed.date ?? "",
      text: parsed.text ?? "",
      html: parsed.html ?? "",
    };

    try {
      await env.NEWSLETTERS.put(key, JSON.stringify(payload), {
        expirationTtl: TTL_SECONDS,
      });
    } catch (e) {
      console.error("KV put failed:", e);
    }

    // 3. Forward the original to Gmail so subscription-confirmation links work.
    if (env.FORWARD_TO) {
      try {
        await message.forward(env.FORWARD_TO);
      } catch (e) {
        // Forward failure is non-fatal: parsing already succeeded, the digest
        // will still pick the email up. Common cause: destination not yet
        // verified in Cloudflare Email Routing.
        console.error(`forward to ${env.FORWARD_TO} failed:`, e);
      }
    }
  },
} satisfies ExportedHandler<Env>;
