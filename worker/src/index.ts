interface Env {
  RESEND_API_KEY: string;
  RESEND_AUDIENCE_ID: string;
}

const ALLOWED_ORIGIN = "https://news.lavillerose.com";
const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const cors = corsHeaders(request);

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: cors });
    }

    const url = new URL(request.url);
    if (request.method !== "POST" || url.pathname !== "/subscribe") {
      return json({ ok: false, error: "not_found" }, 404, cors);
    }

    let email: string;
    try {
      const ct = request.headers.get("Content-Type") || "";
      if (ct.includes("application/json")) {
        const body = (await request.json()) as { email?: string };
        email = (body.email ?? "").trim().toLowerCase();
      } else {
        const form = await request.formData();
        email = String(form.get("email") ?? "").trim().toLowerCase();
      }
    } catch {
      return json({ ok: false, error: "invalid_body" }, 400, cors);
    }

    if (!email || email.length > 254 || !EMAIL_RE.test(email)) {
      return json({ ok: false, error: "invalid_email" }, 400, cors);
    }

    const resp = await fetch(
      `https://api.resend.com/audiences/${env.RESEND_AUDIENCE_ID}/contacts`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${env.RESEND_API_KEY}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ email, unsubscribed: false }),
      },
    );

    if (resp.ok) {
      return json({ ok: true }, 200, cors);
    }

    if (resp.status === 409 || resp.status === 422) {
      return json({ ok: true, note: "already_subscribed" }, 200, cors);
    }

    return json({ ok: false, error: "upstream", status: resp.status }, 502, cors);
  },
} satisfies ExportedHandler<Env>;

function corsHeaders(req: Request): Record<string, string> {
  const origin = req.headers.get("Origin");
  const allowed = origin === ALLOWED_ORIGIN ? ALLOWED_ORIGIN : ALLOWED_ORIGIN;
  return {
    "Access-Control-Allow-Origin": allowed,
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "86400",
    Vary: "Origin",
  };
}

function json(data: unknown, status: number, cors: Record<string, string>): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { ...cors, "Content-Type": "application/json" },
  });
}
