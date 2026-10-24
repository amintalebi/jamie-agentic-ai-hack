// The server writes screens in OpenUI Lang (server/dashboard.py). These tests parse and render the
// sample screens it dumps (npm run fixtures) so a signature mismatch between the two sides fails here.
import { createParser, Renderer } from "@openuidev/react-lang";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { library } from "./library";

const dir = join(__dirname, "__fixtures__");
const fixtures = readdirSync(dir).filter((f) => f.endsWith(".oui"));

describe("server screens", () => {
  it("has fixtures", () => expect(fixtures.length).toBeGreaterThan(0));

  for (const name of fixtures) {
    const text = readFileSync(join(dir, name), "utf8");

    it(`${name} parses without errors`, () => {
      const result = createParser(library.toJSONSchema(), library.root).parse(text);
      expect(result.meta.errors).toEqual([]);
      expect(result.meta.unresolved).toEqual([]);
    });

    it(`${name} renders`, () => {
      const html = renderToStaticMarkup(<Renderer response={text} library={library} isStreaming={false} />);
      expect(html).toContain("<main");
    });
  }
});
