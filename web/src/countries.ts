// Country picker data for the phone field. Germany is the default; the rest is sorted by name.

export type Country = { iso: string; name: string; dial: string };

/** "DE" -> "🇩🇪" (regional indicator letters). */
export function flag(iso: string): string {
  return String.fromCodePoint(...[...iso.toUpperCase()].map((c) => 0x1f1a5 + c.charCodeAt(0)));
}

export const DEFAULT_COUNTRY: Country = { iso: "DE", name: "Germany", dial: "49" };

export const COUNTRIES: Country[] = [
  DEFAULT_COUNTRY,
  ...([
    { iso: "AT", name: "Austria", dial: "43" },
    { iso: "CH", name: "Switzerland", dial: "41" },
    { iso: "NL", name: "Netherlands", dial: "31" },
    { iso: "BE", name: "Belgium", dial: "32" },
    { iso: "LU", name: "Luxembourg", dial: "352" },
    { iso: "FR", name: "France", dial: "33" },
    { iso: "IT", name: "Italy", dial: "39" },
    { iso: "ES", name: "Spain", dial: "34" },
    { iso: "PT", name: "Portugal", dial: "351" },
    { iso: "GB", name: "United Kingdom", dial: "44" },
    { iso: "IE", name: "Ireland", dial: "353" },
    { iso: "DK", name: "Denmark", dial: "45" },
    { iso: "SE", name: "Sweden", dial: "46" },
    { iso: "NO", name: "Norway", dial: "47" },
    { iso: "FI", name: "Finland", dial: "358" },
    { iso: "PL", name: "Poland", dial: "48" },
    { iso: "CZ", name: "Czechia", dial: "420" },
    { iso: "HU", name: "Hungary", dial: "36" },
    { iso: "RO", name: "Romania", dial: "40" },
    { iso: "GR", name: "Greece", dial: "30" },
    { iso: "UA", name: "Ukraine", dial: "380" },
    { iso: "TR", name: "Turkey", dial: "90" },
    { iso: "IR", name: "Iran", dial: "98" },
    { iso: "IL", name: "Israel", dial: "972" },
    { iso: "AE", name: "United Arab Emirates", dial: "971" },
    { iso: "IN", name: "India", dial: "91" },
    { iso: "CN", name: "China", dial: "86" },
    { iso: "JP", name: "Japan", dial: "81" },
    { iso: "KR", name: "South Korea", dial: "82" },
    { iso: "SG", name: "Singapore", dial: "65" },
    { iso: "AU", name: "Australia", dial: "61" },
    { iso: "US", name: "United States", dial: "1" },
    { iso: "CA", name: "Canada", dial: "1" },
    { iso: "MX", name: "Mexico", dial: "52" },
    { iso: "BR", name: "Brazil", dial: "55" },
    { iso: "ZA", name: "South Africa", dial: "27" },
  ] as Country[]).sort((a, b) => a.name.localeCompare(b.name)),
];

/**
 * The E.164 number for what the user typed in the national field.
 * "+44 20…" or "0044 20…" are taken as full international numbers whatever the picker says;
 * otherwise the trunk 0 is dropped ("0151 …" -> "+49151…").
 */
export function toE164(country: Country, raw: string): string {
  const s = raw.trim().replace(/[\s\-().\/]/g, "");
  if (!s) return "";
  if (s.startsWith("+")) return "+" + s.slice(1).replace(/\D/g, "");
  if (s.startsWith("00")) return "+" + s.slice(2).replace(/\D/g, "");
  const national = s.replace(/\D/g, "").replace(/^0+/, "");
  return national ? `+${country.dial}${national}` : "";
}

/** Split a saved E.164 number back into a country and the national part (longest dial code wins). */
export function splitE164(phone: string): { country: Country; national: string } {
  const digits = phone.replace(/\D/g, "");
  if (!phone.startsWith("+") || !digits) return { country: DEFAULT_COUNTRY, national: phone };
  const match = [...COUNTRIES]
    .sort((a, b) => b.dial.length - a.dial.length)
    .find((c) => digits.startsWith(c.dial));
  // +1 is shared: prefer the US over Canada.
  const country = match?.dial === "1" ? COUNTRIES.find((c) => c.iso === "US")! : match;
  return country ? { country, national: digits.slice(country.dial.length) } : { country: DEFAULT_COUNTRY, national: phone };
}
