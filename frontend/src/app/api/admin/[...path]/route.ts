import { NextRequest, NextResponse } from "next/server";

function getAgentUrl() {
  return process.env.AGENT_INTERNAL_URL || "http://localhost:8000";
}

async function proxy(req: NextRequest, method: string) {
  const { pathname, search } = new URL(req.url);
  const agentPath = pathname.replace(/^\/api\/admin/, "");
  const target = `${getAgentUrl()}/admin${agentPath}${search}`;

  const headers: Record<string, string> = {
    ...Object.fromEntries(req.headers),
    host: "",
  };

  const adminToken = req.headers.get("X-Admin-Token");
  if (adminToken) {
    headers["X-Admin-Token"] = adminToken;
  }

  try {
    const init: RequestInit = { method, headers };

    if (method !== "GET" && method !== "DELETE") {
      init.body = req.body;
      // @ts-expect-error duplex needed for streaming
      init.duplex = "half";
    }

    const upstream = await fetch(target, init);

    const resHeaders = new Headers(upstream.headers);
    resHeaders.set("Access-Control-Allow-Origin", "*");

    return new NextResponse(upstream.body, {
      status: upstream.status,
      headers: resHeaders,
    });
  } catch (err: any) {
    return NextResponse.json(
      { error: "Admin API unreachable", detail: err.message },
      { status: 502 },
    );
  }
}

export async function GET(req: NextRequest) {
  return proxy(req, "GET");
}

export async function POST(req: NextRequest) {
  return proxy(req, "POST");
}

export async function PUT(req: NextRequest) {
  return proxy(req, "PUT");
}

export async function DELETE(req: NextRequest) {
  return proxy(req, "DELETE");
}