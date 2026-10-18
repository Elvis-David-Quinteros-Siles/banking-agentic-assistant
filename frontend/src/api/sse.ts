// Parser de Server-Sent Events sobre un ReadableStream.
//
// Se usa fetch + ReadableStream en lugar de EventSource porque EventSource solo admite GET,
// y /chat/stream es POST (lleva el mensaje en el body).
//
// Detalle importante: la red entrega el stream en fragmentos arbitrarios. Un evento puede
// llegar partido en dos chunks, o varios eventos en un mismo chunk; por eso se acumula en un
// buffer y solo se procesa cuando aparece el separador de evento (línea en blanco).

export interface RawSSEEvent {
  event: string;
  data: string;
}

/** Parsea un bloque "event: x\ndata: y" (ya separado por línea en blanco). */
export function parseBlock(block: string): RawSSEEvent | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith(":")) continue; // comentario / keep-alive
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
  }
  if (dataLines.length === 0) return null;
  return { event, data: dataLines.join("\n") };
}

/** Recorre un stream de bytes y produce eventos SSE completos. */
export async function* readSSE(stream: ReadableStream<Uint8Array>): AsyncGenerator<RawSSEEvent> {
  const reader = stream.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      // stream: true evita romper caracteres UTF-8 multibyte (tildes) partidos entre chunks
      buffer += decoder.decode(value, { stream: true });
      let separator: RegExpExecArray | null;
      while ((separator = /\r?\n\r?\n/.exec(buffer)) !== null) {
        const block = buffer.slice(0, separator.index);
        buffer = buffer.slice(separator.index + separator[0].length);
        const parsed = parseBlock(block);
        if (parsed) yield parsed;
      }
    }
    buffer += decoder.decode();
    const last = parseBlock(buffer);
    if (last) yield last;
  } finally {
    reader.releaseLock();
  }
}
