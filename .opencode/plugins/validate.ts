import type { OpenCode } from "@opencode-ai/plugin";

const ARTICLES_PATTERN = /knowledge\/articles\/.*\.json$/;

export function validate({ $ }: OpenCode) {
  return {
    "tool.execute.after": async (input: any) => {
      if (input.tool !== "write" && input.tool !== "edit") return;

      const filePath: string | undefined =
        input.args?.file_path ?? input.args?.filePath;
      if (!filePath || !ARTICLES_PATTERN.test(filePath)) return;

      try {
        const result = await $`python3 hooks/validate_json.py ${filePath}`.nothrow();
        if (result.exitCode !== 0) {
          console.error(
            `[validate] 校验失败 (${filePath}):\n${result.stderr.toString()}`,
          );
        }
      } catch (err) {
        console.error(`[validate] 执行异常 (${filePath}):`, err);
      }
    },
  };
}
