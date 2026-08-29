import { readdir, readFile } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve("src/i18n/locales");
const referenceLocale = "ja-JP";
const localeEntries = await readdir(root, { withFileTypes: true });
const locales = localeEntries
  .filter((entry) => entry.isDirectory())
  .map((entry) => entry.name)
  .sort();
const files = (await readdir(resolve(root, referenceLocale)))
  .filter((file) => file.endsWith(".json"))
  .sort();

if (!locales.includes(referenceLocale)) {
  throw new Error(`Reference locale directory not found: ${referenceLocale}`);
}

function flatten(value, prefix = "") {
  const entries = [];
  for (const [key, child] of Object.entries(value)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (child && typeof child === "object" && !Array.isArray(child)) {
      entries.push(...flatten(child, path));
    } else {
      entries.push([path, child]);
    }
  }
  return entries;
}

function interpolationVariables(value) {
  if (typeof value !== "string") return [];
  return [...value.matchAll(/\{([A-Za-z][A-Za-z0-9_]*)\}/g)]
    .map(([, variable]) => variable)
    .sort();
}

async function readCatalog(locale) {
  const catalog = {};
  for (const file of files) {
    let parsed;
    try {
      parsed = JSON.parse(await readFile(resolve(root, locale, file), "utf8"));
    } catch (error) {
      throw new Error(`${locale}/${file}: invalid or unreadable JSON (${error.message})`);
    }
    Object.assign(catalog, Object.fromEntries(flatten(parsed)));
  }
  return catalog;
}

const catalogs = Object.fromEntries(await Promise.all(locales.map(async (locale) => [locale, await readCatalog(locale)])));
const reference = catalogs[referenceLocale];
const referenceKeys = new Set(Object.keys(reference));
let failed = false;

for (const locale of locales) {
  const catalog = catalogs[locale];
  const keys = new Set(Object.keys(catalog));
  const missing = [...referenceKeys].filter((key) => !keys.has(key));
  const extra = [...keys].filter((key) => !referenceKeys.has(key));
  const empty = Object.entries(catalog)
    .filter(([, value]) => typeof value !== "string" || value.trim() === "")
    .map(([key]) => key);
  const placeholderMismatches = [...referenceKeys]
    .filter((key) => JSON.stringify(interpolationVariables(reference[key])) !== JSON.stringify(interpolationVariables(catalog[key])))
    .map((key) => `${key} (${interpolationVariables(reference[key]).join(", ")} -> ${interpolationVariables(catalog[key]).join(", ")})`);

  if (missing.length || extra.length || empty.length || placeholderMismatches.length) {
    failed = true;
    console.error(`${locale}: ${keys.size} keys`);
    if (missing.length) console.error(`  missing: ${missing.join(", ")}`);
    if (extra.length) console.error(`  extra: ${extra.join(", ")}`);
    if (empty.length) console.error(`  empty: ${empty.join(", ")}`);
    if (placeholderMismatches.length) console.error(`  interpolation variables differ: ${placeholderMismatches.join(", ")}`);
  } else {
    console.log(`${locale}: ${keys.size} keys`);
  }
}

if (failed) process.exit(1);
