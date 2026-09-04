import { json, type ActionFunctionArgs } from "@remix-run/node";
import { writeFile } from "node:fs/promises";
import path from "node:path";

/**
 * Write a finished run to disk as the worked example. Development only.
 *
 * The example page is a real run of the page for your own clips, so it has to
 * be lifted out of a browser that has just done one: the clips are decoded
 * there and nowhere else. This route is how it lands in the repository, and it
 * refuses to do anything in a deployed build.
 */
export async function action({ request }: ActionFunctionArgs) {
  if (process.env.NODE_ENV === "production") {
    return json({ error: "not available" }, { status: 404 });
  }

  const body = await request.text();
  const target = path.join(process.cwd(), "app", "example.json");
  await writeFile(target, `${JSON.stringify(JSON.parse(body), null, 2)}\n`, "utf-8");
  return json({ written: target, bytes: body.length });
}
