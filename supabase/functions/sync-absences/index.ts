import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

type Absence = {
  teacher: string;
  periods_impacted: string;
};

type SyncPayload = {
  date: string;
  absences: Absence[];
};

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const SUPABASE_SERVICE_ROLE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const SYNC_SECRET = Deno.env.get("SYNC_SECRET")!;

const supabase = createClient(
  SUPABASE_URL,
  SUPABASE_SERVICE_ROLE_KEY,
);

const ALL_PERIODS = [
  "igs",
  "1",
  "2",
  "3",
  "4",
  "5",
  "6",
  "7",
  "8",
  "9",
];

function normalizePeriods(rawValue: string): string {
  if (!rawValue) return "";

  let text = rawValue
    .replace(/[\u2013\u2014]/g, "-")
    .replace(/\u00A0/g, " ")
    .trim();

  // "all" always means every period.
  if (/\ball\b/i.test(text)) {
    return ALL_PERIODS.join(", ");
  }

  // Pre-normalize common connectors and words:
  text = text
    .replace(/\b(?:through|thru|to)\b/gi, "-")
    .replace(/&|\band\b|\+|\/|;/gi, ",")
    .replace(/\b(?:periods?|mods?|p\.?)\b/gi, " ")
    .replace(/\s*-\s*/g, "-");

  const periods = new Set<string>();

  const tokens = text
    .split(",")
    .map((token) => token.trim().toLowerCase())
    .filter(Boolean);

  for (const token of tokens) {
    if (token === "igs") {
      periods.add("igs");
      continue;
    }

    // Numerical range, e.g. 1-4, 2-5.
    const rangeMatch = token.match(/^([1-9])-([1-9])$/);
    if (rangeMatch) {
      const start = Number(rangeMatch[1]);
      const end = Number(rangeMatch[2]);
      if (start <= end) {
        for (let period = start; period <= end; period++) {
          periods.add(String(period));
        }
      }
      continue;
    }

    // Single numerical period.
    if (/^[1-9]$/.test(token)) {
      periods.add(token);
      continue;
    }

    // Standalone digit fallback
    const digits = token.match(/\b[1-9]\b/g);
    if (digits) {
      for (const d of digits) {
        periods.add(d);
      }
    }
  }

  return [
    ...(periods.has("igs") ? ["igs"] : []),
    ...Array.from({ length: 9 }, (_, i) => String(i + 1))
      .filter((period) => periods.has(period)),
  ].join(", ");
}

function normalizeAbsences(absences: Absence[]): Absence[] {
  return absences
    .map((absence) => ({
      teacher: absence.teacher.trim(),
      periods_impacted: normalizePeriods(absence.periods_impacted),
    }))
    .filter(
      (absence) =>
        absence.teacher.length > 0 &&
        absence.periods_impacted.length > 0,
    )
    .sort((a, b) => a.teacher.localeCompare(b.teacher));
}

function getTodayInNewYork(): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
}

function jsonResponse(
  body: Record<string, unknown>,
  status = 200,
): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json",
    },
  });
}

Deno.serve(async (req) => {
  try {
    if (req.method !== "POST") {
      return jsonResponse(
        { error: "Method not allowed" },
        405,
      );
    }

    const authorization = req.headers.get("Authorization");

    if (authorization !== `Bearer ${SYNC_SECRET}`) {
      return jsonResponse(
        { error: "Unauthorized" },
        401,
      );
    }

    const payload = (await req.json()) as SyncPayload;

    if (
      !payload ||
      typeof payload.date !== "string" ||
      !Array.isArray(payload.absences)
    ) {
      return jsonResponse(
        { error: "Invalid payload" },
        400,
      );
    }

    const normalizedAbsences = normalizeAbsences(payload.absences);
    const today = getTodayInNewYork();

    /*
     * The Supabase table is a LIVE representation of today's
     * cancellation data.
     *
     * If the Sheet contains anything other than today's date,
     * the database must be empty.
     */
    if (payload.date !== today) {
      const { error } = await supabase
        .from("teacher_absences")
        .delete()
        .not("id", "is", null);

      if (error) {
        console.error("Failed to clear stale data:", error);

        return jsonResponse(
          {
            error: "Failed to clear stale absence data",
            details: error.message,
          },
          500,
        );
      }

      return jsonResponse({
        success: true,
        changed: true,
        cleared: true,
        reason: "Sheet date is not today",
        sheet_date: payload.date,
        today,
      });
    }

    /*
     * Get the current live state.
     */
    const { data: currentRows, error: fetchError } = await supabase
      .from("teacher_absences")
      .select("teacher, periods_impacted")
      .eq("date", today)
      .order("teacher", { ascending: true });

    if (fetchError) {
      console.error("Failed to fetch current data:", fetchError);

      return jsonResponse(
        {
          error: "Failed to fetch current absence data",
          details: fetchError.message,
        },
        500,
      );
    }

    // Fetch registered teachers and aliases for canonical name resolution
    const { data: teacherRows } = await supabase
      .from("teachers")
      .select("name, aliases");

    const knownTeachers: Array<{ name: string; aliases: string[] }> = (teacherRows || []).map((t: any) => ({
      name: t.name,
      aliases: Array.isArray(t.aliases) ? t.aliases : [],
    }));

    function resolveTeacherName(raw: string): string {
      const lower = raw.trim().toLowerCase();
      for (const t of knownTeachers) {
        if (t.name.trim().toLowerCase() === lower) return t.name;
        for (const a of t.aliases) {
          if (a.trim().toLowerCase() === lower) return t.name;
        }
      }
      return raw.trim();
    }

    const currentAbsences = normalizeAbsences(
      (currentRows ?? []).map((row) => ({
        teacher: resolveTeacherName(row.teacher),
        periods_impacted: row.periods_impacted,
      })),
    );

    const canonicalIncoming = normalizeAbsences(
      payload.absences.map((a) => ({
        teacher: resolveTeacherName(a.teacher),
        periods_impacted: a.periods_impacted,
      })),
    );

    const currentSnapshot = JSON.stringify(currentAbsences);
    const incomingSnapshot = JSON.stringify(canonicalIncoming);

    /*
     * Nothing changed. Leave Supabase completely untouched.
     */
    if (currentSnapshot === incomingSnapshot) {
      return jsonResponse({
        success: true,
        changed: false,
        cleared: false,
        date: today,
        count: canonicalIncoming.length,
      });
    }

    /*
     * Compute difference events for push notifications
     */
    const oldMap = new Map(currentAbsences.map(a => [a.teacher.toLowerCase(), a]));
    const newMap = new Map(canonicalIncoming.map(a => [a.teacher.toLowerCase(), a]));

    const absenceEvents: Array<{
      type: "inserted" | "updated" | "removed";
      teacher: string;
      periodsImpacted?: string;
    }> = [];

    // Check for newly inserted or updated absences
    for (const [key, newAbsence] of newMap.entries()) {
      const oldAbsence = oldMap.get(key);
      if (!oldAbsence) {
        absenceEvents.push({
          type: "inserted",
          teacher: newAbsence.teacher,
          periodsImpacted: newAbsence.periods_impacted,
        });
      } else if (oldAbsence.periods_impacted !== newAbsence.periods_impacted) {
        absenceEvents.push({
          type: "updated",
          teacher: newAbsence.teacher,
          periodsImpacted: newAbsence.periods_impacted,
        });
      }
    }

    // Check for removed absences
    for (const [key, oldAbsence] of oldMap.entries()) {
      if (!newMap.has(key)) {
        absenceEvents.push({
          type: "removed",
          teacher: oldAbsence.teacher,
        });
      }
    }

    /*
     * Something changed.
     *
     * Replace the entire live snapshot. This handles:
     * - new teachers
     * - removed teachers
     * - changed periods
     * - all -> specific periods
     * - specific periods -> all
     * - going from data -> zero absences
     */
    const { error: deleteError } = await supabase
      .from("teacher_absences")
      .delete()
      .not("id", "is", null);

    if (deleteError) {
      console.error("Failed to clear old snapshot:", deleteError);

      return jsonResponse(
        {
          error: "Failed to clear old absence snapshot",
          details: deleteError.message,
        },
        500,
      );
    }

    /*
     * Empty snapshot is valid. The table simply remains empty.
     */
    if (canonicalIncoming.length === 0) {
      // If there were removals, dispatch notifications
      if (absenceEvents.length > 0) {
        dispatchAbsenceNotifications(absenceEvents);
      }

      return jsonResponse({
        success: true,
        changed: true,
        cleared: true,
        date: today,
        count: 0,
      });
    }

    const syncedAt = new Date().toISOString();

    const rowsToInsert = canonicalIncoming.map((absence) => ({
      date: today,
      synced_at: syncedAt,
      teacher: absence.teacher,
      periods_impacted: absence.periods_impacted,
    }));

    const { error: insertError } = await supabase
      .from("teacher_absences")
      .insert(rowsToInsert);

    if (insertError) {
      console.error("Failed to insert new snapshot:", insertError);

      return jsonResponse(
        {
          error: "Failed to insert new absence snapshot",
          details: insertError.message,
        },
        500,
      );
    }

    // Dispatch remote push notifications to Cloudflare Worker backend
    if (absenceEvents.length > 0) {
      await dispatchAbsenceNotifications(absenceEvents);
    }

    return jsonResponse({
      success: true,
      changed: true,
      cleared: false,
      date: today,
      count: canonicalIncoming.length,
      synced_at: syncedAt,
      notifiedEvents: absenceEvents.length,
    });
  } catch (error) {
    console.error("Unexpected error:", error);

    return jsonResponse(
      {
        error: "Internal server error",
        details: error instanceof Error ? error.message : String(error),
      },
      500,
    );
  }
});

async function dispatchAbsenceNotifications(
  events: Array<{ type: string; teacher: string; periodsImpacted?: string }>
): Promise<void> {
  const workerUrl =
    Deno.env.get("NOTIFICATION_BACKEND_URL") ||
    "https://bcaway-notifications.tjaynj.workers.dev";

  try {
    console.log(
      `Dispatching ${events.length} absence notification event(s) to ${workerUrl}...`
    );
    const resp = await fetch(`${workerUrl}/notify-absence`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ events }),
    });

    const respText = await resp.text();
    console.log(`Notification worker response (${resp.status}):`, respText);
  } catch (err) {
    console.error("Failed to dispatch absence notifications to worker:", err);
  }
}