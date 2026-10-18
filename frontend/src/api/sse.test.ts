import { describe, expect, it } from "vitest";
import { parseBlock, readSSE } from "./sse";

// Crea un ReadableStream que entrega el texto en los fragmentos indicados (simula la red)
function streamFrom(chunks: (string | Uint8Array)[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(typeof chunk === "string" ? encoder.encode(chunk) : chunk);
      controller.close();
    },
  });
}

async function collect(stream: ReadableStream<Uint8Array>) {
  const events = [];
  for await (const event of readSSE(stream)) events.push(event);
  return events;
}

describe("parseBlock", () => {
  it("lee event y data", () => {
    expect(parseBlock('event: token\ndata: {"delta":"hola"}')).toEqual({ event: "token", data: '{"delta":"hola"}' });
  });

  it("ignora comentarios y bloques sin data", () => {
    expect(parseBlock(": keep-alive")).toBeNull();
  });
});

describe("readSSE", () => {
  it("separa varios eventos que llegan en un mismo chunk", async () => {
    const events = await collect(streamFrom(['event: meta\ndata: {"thread_id":"t1"}\n\nevent: token\ndata: {"delta":"Hola"}\n\n']));
    expect(events.map((e) => e.event)).toEqual(["meta", "token"]);
  });

  it("reconstruye un evento partido entre chunks", async () => {
    const events = await collect(streamFrom(["event: tok", 'en\ndata: {"del', 'ta":"x"}\n', "\n"]));
    expect(events).toEqual([{ event: "token", data: '{"delta":"x"}' }]);
  });

  it("no rompe tildes partidas a la mitad de un carácter UTF-8", async () => {
    const bytes = new TextEncoder().encode('event: token\ndata: {"delta":"intención"}\n\n');
    const cut = bytes.indexOf(0xc3) + 1; // corta entre los dos bytes de "ó"
    const events = await collect(streamFrom([bytes.slice(0, cut), bytes.slice(cut)]));
    expect(JSON.parse(events[0].data).delta).toBe("intención");
  });

  it("acepta separadores CRLF", async () => {
    const events = await collect(streamFrom(['event: final\r\ndata: {"ok":true}\r\n\r\n']));
    expect(events).toEqual([{ event: "final", data: '{"ok":true}' }]);
  });
});
