import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const SUPABASE_SERVICE_ROLE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const SYNC_SECRET = Deno.env.get("SYNC_SECRET") || "";

const supabase = createClient(
  SUPABASE_URL,
  SUPABASE_SERVICE_ROLE_KEY,
);

function secureCompare(a: string, b: string): boolean {
  if (!a || !b) return false;
  const aBytes = new TextEncoder().encode(a);
  const bBytes = new TextEncoder().encode(b);
  if (aBytes.byteLength !== bBytes.byteLength) return false;
  return crypto.subtle.timingSafeEqual(aBytes, bBytes);
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

type ManagePayload =
  | { action: "list" }
  | { action: "add_teacher"; name: string; aliases?: string[] }
  | { action: "add_alias"; teacherId: string; alias: string }
  | { action: "remove_alias"; teacherId: string; alias: string }
  | { action: "delete_teacher"; teacherId: string };

Deno.serve(async (req) => {
  try {
    if (req.method !== "POST" && req.method !== "GET") {
      return jsonResponse({ error: "Method not allowed" }, 405);
    }

    const authorization = req.headers.get("Authorization");
    const token = authorization
      ? authorization.replace(/^Bearer\s+/i, "").replace(/[\s\r\n\t]+/g, "")
      : "";
    const rawSecret = SYNC_SECRET.replace(/[\s\r\n\t]+/g, "");
    const unquotedSecret = rawSecret.replace(/^["']+|["']+$/g, "").replace(/[\s\r\n\t]+/g, "");
    const serviceRoleKey = (SUPABASE_SERVICE_ROLE_KEY || "").replace(/[\s\r\n\t]+/g, "");

    const isAuthorized = Boolean(
      token && (
        (serviceRoleKey && secureCompare(token, serviceRoleKey)) ||
        (rawSecret && secureCompare(token, rawSecret)) ||
        (unquotedSecret && secureCompare(token, unquotedSecret))
      )
    );

    if (!isAuthorized) {
      console.error("Unauthorized request to manage-teachers");
      return jsonResponse({ error: "Unauthorized" }, 401);
    }

    if (req.method === "GET") {
      const { data, error } = await supabase
        .from("teachers")
        .select("id, name, aliases, created_at, updated_at")
        .order("name", { ascending: true });

      if (error) {
        return jsonResponse({ error: error.message }, 500);
      }
      return jsonResponse({ success: true, teachers: data || [] });
    }

    const payload = (await req.json()) as ManagePayload;
    if (!payload || !payload.action) {
      return jsonResponse({ error: "Invalid payload: action is required" }, 400);
    }

    switch (payload.action) {
      case "list": {
        const { data, error } = await supabase
          .from("teachers")
          .select("id, name, aliases, created_at, updated_at")
          .order("name", { ascending: true });

        if (error) {
          return jsonResponse({ error: error.message }, 500);
        }
        return jsonResponse({ success: true, teachers: data || [] });
      }

      case "add_teacher": {
        const cleanName = (payload.name || "").trim();
        if (!cleanName) {
          return jsonResponse({ error: "Teacher name cannot be empty" }, 400);
        }
        const cleanAliases = Array.isArray(payload.aliases)
          ? payload.aliases.map((a: string) => a.trim()).filter(Boolean)
          : [];

        const { data, error } = await supabase
          .from("teachers")
          .insert({
            name: cleanName,
            aliases: cleanAliases,
          })
          .select("id, name, aliases, created_at, updated_at")
          .single();

        if (error) {
          return jsonResponse({ error: error.message }, 400);
        }
        return jsonResponse({ success: true, teacher: data });
      }

      case "add_alias": {
        const { teacherId, alias } = payload;
        const cleanAlias = (alias || "").trim();
        if (!teacherId || !cleanAlias) {
          return jsonResponse({ error: "teacherId and alias are required" }, 400);
        }

        const { data, error: fetchError } = await supabase
          .from("teachers")
          .select("aliases")
          .eq("id", teacherId)
          .single();

        if (fetchError || !data) {
          return jsonResponse({ error: fetchError?.message || "Teacher not found" }, 404);
        }

        const currentAliases: string[] = Array.isArray(data.aliases) ? data.aliases : [];
        if (currentAliases.some((a) => a.toLowerCase() === cleanAlias.toLowerCase())) {
          return jsonResponse({ success: true, aliases: currentAliases });
        }

        const updatedAliases = [...currentAliases, cleanAlias];
        const { error: updateError } = await supabase
          .from("teachers")
          .update({
            aliases: updatedAliases,
            updated_at: new Date().toISOString(),
          })
          .eq("id", teacherId);

        if (updateError) {
          return jsonResponse({ error: updateError.message }, 500);
        }
        return jsonResponse({ success: true, aliases: updatedAliases });
      }

      case "remove_alias": {
        const { teacherId, alias } = payload;
        const cleanAlias = (alias || "").trim();
        if (!teacherId || !cleanAlias) {
          return jsonResponse({ error: "teacherId and alias are required" }, 400);
        }

        const { data, error: fetchError } = await supabase
          .from("teachers")
          .select("aliases")
          .eq("id", teacherId)
          .single();

        if (fetchError || !data) {
          return jsonResponse({ error: fetchError?.message || "Teacher not found" }, 404);
        }

        const currentAliases: string[] = Array.isArray(data.aliases) ? data.aliases : [];
        const updatedAliases = currentAliases.filter(
          (a) => a.trim().toLowerCase() !== cleanAlias.toLowerCase(),
        );

        const { error: updateError } = await supabase
          .from("teachers")
          .update({
            aliases: updatedAliases,
            updated_at: new Date().toISOString(),
          })
          .eq("id", teacherId);

        if (updateError) {
          return jsonResponse({ error: updateError.message }, 500);
        }
        return jsonResponse({ success: true, aliases: updatedAliases });
      }

      case "delete_teacher": {
        const { teacherId } = payload;
        if (!teacherId) {
          return jsonResponse({ error: "teacherId is required" }, 400);
        }

        const { error } = await supabase
          .from("teachers")
          .delete()
          .eq("id", teacherId);

        if (error) {
          return jsonResponse({ error: error.message }, 500);
        }
        return jsonResponse({ success: true, deleted: teacherId });
      }

      default:
        return jsonResponse({ error: "Unsupported action" }, 400);
    }
  } catch (error) {
    console.error("Unexpected error in manage-teachers:", error);
    return jsonResponse(
      {
        error: "Internal server error",
        details: error instanceof Error ? error.message : String(error),
      },
      500,
    );
  }
});
