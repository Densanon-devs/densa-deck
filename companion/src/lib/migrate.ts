/**
 * Bringing an already-installed database up to the current schema.
 *
 * Every table here is created with `CREATE TABLE IF NOT EXISTS`, which
 * is exactly right the first time and does nothing at all afterwards.
 * Add a column to one of those statements and a fresh install gets it
 * while every phone that already had the app keeps the old table --
 * silently, because the CREATE succeeds by doing nothing.
 *
 * That is not hypothetical. Prices were added to the catalogue and the
 * upgrade path answered
 *
 *   no such column: price_usd
 *
 * on the first phone to install over the top, which is the normal way
 * to get a new build. The column existed in the source and not in the
 * file.
 *
 * So the schema stays declarative -- one CREATE TABLE per table, the
 * single description of what that table holds -- and this reads those
 * statements back, compares them with what the file actually has, and
 * adds whatever is missing. Nothing to remember when adding a column,
 * which is the point: the last one was not forgotten out of
 * carelessness, it was forgotten because nothing asked.
 *
 * What it deliberately does NOT do: drop columns, change types, or
 * rewrite tables. Those need thought about the data already in them,
 * and a migration that quietly rewrites someone's collection is worse
 * than one that refuses.
 */

/** A column as the schema declares it. */
export interface PlannedColumn {
  name: string;
  /** The full declaration, which is what ALTER TABLE wants. */
  definition: string;
}

/** What one CREATE TABLE statement says the table should hold. */
export interface TablePlan {
  table: string;
  columns: PlannedColumn[];
}

const CREATE_TABLE =
  /^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][\w]*)\s*\(([\s\S]*)\)\s*;?\s*$/i;

/**
 * Table-level constraints, which look like columns to a naive split.
 *
 * `PRIMARY KEY (a, b)` is not a column called PRIMARY.
 */
const CONSTRAINT = /^(PRIMARY|UNIQUE|CHECK|FOREIGN|CONSTRAINT)\b/i;

/**
 * Strip comments, which the schema uses heavily.
 *
 * Both kinds. A block comment sitting in front of a column was read
 * as that column's name, so the real name was never seen, was treated
 * as missing, and was added again -- `duplicate column name: pushed`
 * on the second launch, which is every launch after the first. Blocks
 * go first: a `--` inside one would otherwise cut the line short and
 * orphan the closing marker.
 */
function uncommented(sql: string): string {
  return sql
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .split('\n')
    .map((line) => {
      const cut = line.indexOf('--');
      return cut === -1 ? line : line.slice(0, cut);
    })
    .join('\n');
}

/**
 * Split on commas that separate columns, not commas inside brackets.
 *
 * `PRIMARY KEY (series_key, captured_on)` is one part, and splitting
 * it in two would invent a column named `captured_on)`.
 */
function topLevelParts(body: string): string[] {
  const out: string[] = [];
  let depth = 0;
  let start = 0;
  for (let i = 0; i < body.length; i += 1) {
    const ch = body[i];
    if (ch === '(') depth += 1;
    else if (ch === ')') depth -= 1;
    else if (ch === ',' && depth === 0) {
      out.push(body.slice(start, i));
      start = i + 1;
    }
  }
  out.push(body.slice(start));
  return out;
}

/**
 * What a CREATE TABLE statement declares, or null if it is not one.
 *
 * The schema list also holds CREATE INDEX statements; they need no
 * migration because `IF NOT EXISTS` genuinely does create a missing
 * index.
 */
export function tablePlan(sql: string): TablePlan | null {
  const match = CREATE_TABLE.exec(uncommented(sql));
  if (!match?.[1] || !match[2]) return null;
  const columns: PlannedColumn[] = [];
  for (const part of topLevelParts(match[2])) {
    const definition = part.trim().replace(/\s+/g, ' ');
    if (!definition) continue;
    if (CONSTRAINT.test(definition)) continue;
    const name = definition.split(/[\s(]/)[0] ?? '';
    if (name) columns.push({ name, definition });
  }
  return { table: match[1], columns };
}

/**
 * Whether SQLite will accept this column being added to a live table.
 *
 * It refuses a PRIMARY KEY or UNIQUE column outright, and refuses NOT
 * NULL without a default because the rows already there would have to
 * hold something. Attempting one throws, and a throw here takes the
 * whole database open down with it -- so these are reported rather
 * than attempted, and a column that needs one of them needs a real
 * migration written by hand.
 */
export function canAddLive(definition: string): boolean {
  const upper = definition.toUpperCase();
  if (/\bPRIMARY\s+KEY\b/.test(upper)) return false;
  if (/\bUNIQUE\b/.test(upper)) return false;
  if (/\bNOT\s+NULL\b/.test(upper) && !/\bDEFAULT\b/.test(upper)) return false;
  return true;
}

/** The statements that bring one existing table up to its plan. */
export function addColumnStatements(
  plan: TablePlan,
  existing: string[],
): { statements: string[]; unsupported: string[] } {
  const have = new Set(existing.map((c) => c.toLowerCase()));
  const statements: string[] = [];
  const unsupported: string[] = [];
  for (const column of plan.columns) {
    if (have.has(column.name.toLowerCase())) continue;
    if (!canAddLive(column.definition)) {
      unsupported.push(`${plan.table}.${column.name}`);
      continue;
    }
    // The table and the definition both come from this file's own
    // schema constant, never from anything a user or a card typed.
    statements.push(`ALTER TABLE ${plan.table} ADD COLUMN ${column.definition}`);
  }
  return { statements, unsupported };
}
