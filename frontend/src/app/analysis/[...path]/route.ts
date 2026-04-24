import { NextRequest, NextResponse } from "next/server";

function getAnalysisUrl() {
  return process.env.ANALYSIS_INTERNAL_URL || "http://localhost:8095";
}

export async function GET(req: NextRequest) {
  const { pathname, search } = new URL(req.url);
  const analysisPath = pathname.replace(/^\/analysis/, "");
  const target = `${getAnalysisUrl()}${analysisPath}${search}`;

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
      { error: "Analysis service unreachable", detail: err.message },
      { status: 502 }
    );
  }
}

export async function POST(req: NextRequest) {
  const { pathname, search } = new URL(req.url);
  const analysisPath = pathname.replace(/^\/analysis/, "");
  const target = `${getAnalysisUrl()}${analysisPath}${search}`;

  try {
    const upstream = await fetch(target, {
      method: "POST",
      headers: { ...Object.fromEntries(req.headers), host: "" },
      body: req.body,
      // @ts-expect-error duplex needed for streaming
      duplex: "half",
    });

    const resHeaders = new Headers(upstream.headers);
    resHeaders.set("Access-Control-Allow-Origin", "*");

    return new NextResponse(upstream.body, {
      status: upstream.status,
      headers: resHeaders,
    });
  } catch (err: any) {
    return NextResponse.json(
      { error: "Analysis service unreachable", detail: err.message },
      { status: 502 }
    );
  }
}