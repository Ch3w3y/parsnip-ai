import { NextRequest, NextResponse } from "next/server";

function getAgentUrl() {
  return process.env.AGENT_INTERNAL_URL || "http://localhost:8000";
}

export async function POST(req: NextRequest) {
  const { pathname, search } = new URL(req.url);
  // /api/chat/completions → /v1/chat/completions
  // /api/chat/... → /v1/chat/...
  const agentPath = pathname.replace(/^\/api\/chat/, "/v1/chat");
  const target = `${getAgentUrl()}${agentPath}${search}`;

  const headers = new Headers(req.headers);
  headers.delete("host");

  try {
    const upstream = await fetch(target, {
      method: "POST",
      headers,
      body: req.body,
      // @ts-expect-error duplex is needed for streaming
      duplex: "half",
    });

    const resHeaders = new Headers(upstream.headers);
    resHeaders.set("Access-Control-Allow-Origin", "*");

    // Streaming: pass through the thread ID header
    const threadId = upstream.headers.get("x-thread-id");
    if (threadId) {
      resHeaders.set("x-thread-id", threadId);
    }

    return new NextResponse(upstream.body, {
      status: upstream.status,
      headers: resHeaders,
    });
  } catch (err: any) {
    return NextResponse.json(
      { error: "Agent unreachable", detail: err.message },
      { status: 502 }
    );
  }
}

export async function GET(req: NextRequest) {
  const { pathname, search } = new URL(req.url);
  const agentPath = pathname.replace(/^\/api\/chat/, "/v1/chat");
  const target = `${getAgentUrl()}${agentPath}${search}`;

  try {
    const upstream = await fetch(target, {
      method: "GET",
      headers: { ...Object.fromEntries(req.headers), host: "" },
    });

    const resHeaders = new Headers(upstream.headers);
    resHeaders.set("Access-Control-Allow-Origin", "*");

    return new NextResponse(upstream.body, {
      status: upstream.status,
      headers: resHeaders,
    });
  } catch (err: any) {
    return NextResponse.json(
      { error: "Agent unreachable", detail: err.message },
      { status: 502 }
    );
  }
}