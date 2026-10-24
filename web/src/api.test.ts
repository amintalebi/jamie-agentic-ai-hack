import { describe, expect, it } from "vitest";
import { stripToken, tokenFromHash } from "./api";

describe("token links", () => {
  it.each([
    ["#token=T", "T", "#/"],
    ["#/?token=T", "T", "#/"],
    ["#/calls/abc?token=T", "T", "#/calls/abc"],
    ["#/calls/abc?token=T&x=1", "T", "#/calls/abc?x=1"],
    ["#/calls/abc?x=1&token=T", "T", "#/calls/abc?x=1"],
    ["#/calls/abc", null, "#/calls/abc"],
    ["", null, "#/"],
  ])("%s", (hash, token, rest) => {
    expect(tokenFromHash(hash)).toBe(token);
    expect(stripToken(hash)).toBe(rest);
  });
});
