import { describe, expect, it } from "vitest";
import { normalisePhone } from "./api";

describe("normalisePhone", () => {
  it.each([
    ["+49 151 0000 1234", "+4915100001234"],
    ["+49-151-0000-1234", "+4915100001234"],
    ["(555) 123-4567", "+5551234567"],
    ["  015100001234", "+015100001234"],
    ["  ", ""],
  ])("%s -> %s", (raw, expected) => {
    expect(normalisePhone(raw)).toBe(expected);
  });
});