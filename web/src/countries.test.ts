import { describe, expect, it } from "vitest";
import { COUNTRIES, DEFAULT_COUNTRY, flag, splitE164, toE164 } from "./countries";

const uk = COUNTRIES.find((c) => c.iso === "GB")!;

describe("countries", () => {
  it("defaults to Germany", () => {
    expect(COUNTRIES[0]).toBe(DEFAULT_COUNTRY);
    expect(DEFAULT_COUNTRY.dial).toBe("49");
  });

  it("makes flag emojis", () => {
    expect(flag("DE")).toBe("🇩🇪");
    expect(flag("gb")).toBe("🇬🇧");
  });

  it.each([
    [DEFAULT_COUNTRY, "0151 0000 1234", "+4915100001234"],
    [DEFAULT_COUNTRY, "151 0000 1234", "+4915100001234"],
    [DEFAULT_COUNTRY, "+44 20 7946 0000", "+442079460000"],
    [DEFAULT_COUNTRY, "0044 20 7946 0000", "+442079460000"],
    [uk, "020 7946 0000", "+442079460000"],
    [DEFAULT_COUNTRY, "  ", ""],
  ])("toE164(%s, %s) -> %s", (country, raw, expected) => {
    expect(toE164(country, raw)).toBe(expected);
  });

  it("splits a saved number back into country and national part", () => {
    expect(splitE164("+4915100001234")).toEqual({ country: DEFAULT_COUNTRY, national: "15100001234" });
    expect(splitE164("+442079460000").country.iso).toBe("GB");
    expect(splitE164("+15551234567").country.iso).toBe("US");
    expect(splitE164("+35312345678").country.iso).toBe("IE"); // 353 beats 35x and 3x
    expect(splitE164("").country).toBe(DEFAULT_COUNTRY);
  });
});
