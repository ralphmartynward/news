import PostalMime from "postal-mime";

interface Env {
  NEWSLETTERS: KVNamespace;
  FORWARD_TO: string;
}

const TTL_SECONDS = 7 * 24 * 60 * 60; // 7 days

/** Extract the best HTML + original From address from a parsed email.
 *
 * Gmail auto-forward rewrites the outer From to the Gmail address and
 * sometimes wraps the original message as a message/rfc822 attachment or
 * inline part.  When parsed.html is empty we try to recover the original
 * email by re-parsing any message/rfc822 attachment with PostalMime.
 */
async function extractContent(
  parsed: Awaited<ReturnType<PostalMime["parse"]>>,
  outerFrom: string,
): Promise<{ html: string; text: string; from: string }> {
  // Happy path: outer email has HTML content.
  if (parsed.html) {
    return { html: parsed.html, text: parsed.text ?? "", from: outerFrom };
  }

  // Attempt to recover from a forwarded-message attachment (message/rfc822).
  for (const att of parsed.attachments ?? []) {
    if (
      att.mimeType?.toLowerCase().includes("message/rfc822") &&
      att.content instanceof ArrayBuffer &&
      att.content.byteLength > 0
    ) {
      try {
        const inner = await new PostalMime().parse(att.content);
        if (inner.html) {
          // Prefer the original sender from the inner message if available.
          const innerFrom =
            inner.from?.address ?? inner.from?.name ?? outerFrom;
          return {
            html: inner.html,
            text: inner.text ?? "",
            from: innerFrom,
          };
        }
      } catch {
        // Inner parse failed — fall through to plain-text fallback.
      }
    }
  }

  // Last resort: return whatever we have (may be plain-text only).
  return { html: "", text: parsed.text ?? "", from: outerFrom };
}

export default {
  async email(message: ForwardableEmailMessage, env: Env): Promise<void> {
    // 1. Parse the MIME envelope so we have structured fields.
    let parsed: Awaited<ReturnType<PostalMime["parse"]>>;
    try {
      const buf = await new Response(message.raw).arrayBuffer();
      parsed = await new PostalMime().parse(buf);
    } catch (e) {
      console.error("MIME parse failed:", e);
      parsed = { subject: "(parse failed)", text: "", html: "", date: "" } as any;
    }

    // 2. Unwrap forwarded messages so the pipeline sees the original content.
    const { html, text, from } = await extractContent(parsed, message.from);

    // 3. Store the parsed payload in KV so the daily digest pipeline can read it.
    const receivedAt = new Date().toISOString();
    const key = `${message.from}:${receivedAt}`;
    const payload = {
      from,
      to: message.to,
      receivedAt,
      subject: parsed.subject ?? "",
      date: parsed.date ?? "",
      text,
      html,
    };

    try {
      await env.NEWSLETTERS.put(key, JSON.stringify(payload), {
        expirationTtl: TTL_SECONDS,
      });
    } catch (e) {
      console.error("KV put failed:", e);
    }

    // 4. Forward the original to Gmail so subscription-confirmation links work.
    if (env.FORWARD_TO) {
      try {
        await message.forward(env.FORWARD_TO);
      } catch (e) {
        console.error(`forward to ${env.FORWARD_TO} failed:`, e);
      }
    }
  },
} satisfies ExportedHandler<Env>;
