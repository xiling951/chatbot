"""Authenticated local WebChat probe. Real model calls use the plugin's allowance."""

import argparse
import json
import time

import httpx
from local_paths import DATA

HOME = DATA


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=0)
    parser.add_argument("--message", default="/记忆帮助")
    parser.add_argument("--suite", choices=["memory"])
    parser.add_argument("--session-id")
    args = parser.parse_args()
    auth = json.loads((HOME / "admin.local.json").read_text(encoding="utf-8"))
    with httpx.Client(base_url=auth["url"], trust_env=False, timeout=50) as client:
        login = client.post(
            "/api/auth/login",
            json={"username": auth["username"], "password": auth["password"]},
        ).json()
        if login.get("status") != "ok":
            raise RuntimeError(
                "Local dashboard login failed: " + str(login.get("message"))
            )
        client.headers["Authorization"] = "Bearer " + login["data"]["token"]
        session = (
            args.session_id
            or client.get("/api/chat/new_session").json()["data"]["session_id"]
        )
        cases = [
            "你好，今天想聊聊音乐。",
            "我刚忙完，有点累。",
            "你是什么身份？",
            "不知道我的生日的话请直说。",
            "我喜欢爵士，给个简短回应。",
            "如果意见不同，你会怎么说？",
            "今天读书读得很慢，有点烦。",
            "给我一句不说教的鼓励。",
            "我们能聊聊海边吗？",
            "你今天真的出门了吗？",
            "请别用愧疚感催我聊天。",
            "用轻松的话接续刚才的话题。",
            "我现在想安静一会儿。",
            "我回来了，你好。",
            "你能替我决定我的身份吗？",
            "我的约定是什么？没说过就说不知道。",
            "我喜欢蓝色。",
            "谢谢你听我说。",
            "请用两句话介绍自己。",
            "测试结束，说一句晚安。",
        ]
        prompts = cases[: args.count] if args.count else [args.message]
        if args.suite == "memory":
            prompts = [
                "/记住 音乐=摇滚",
                "/记忆",
                "/更正 音乐=爵士",
                "/记忆",
                "/停止记忆",
                "/记住 饮料=茶",
                "/恢复记忆",
                "/导出记忆",
                "/忘记 音乐",
                "/记忆",
            ]
        results = []
        for index, prompt in enumerate(prompts):
            started = time.monotonic()
            response = client.post(
                "/api/chat/send",
                json={
                    "session_id": session,
                    "message": prompt,
                    "selected_provider": "companion-deepseek",
                },
            )
            events = []
            for line in response.text.splitlines():
                if line.startswith("data: "):
                    try:
                        events.append(json.loads(line[6:]))
                    except json.JSONDecodeError:
                        pass
            text = "".join(
                str(e.get("data", "")) for e in events if e.get("type") == "plain"
            )
            result = {
                "index": index + 1,
                "http_status": response.status_code,
                "seconds": round(time.monotonic() - started, 3),
                "events": events,
                "text": text,
                "prompt": prompt,
            }
            results.append(result)
            print(
                json.dumps(
                    {k: result[k] for k in ("index", "http_status", "seconds", "text")},
                    ensure_ascii=False,
                ),
                flush=True,
            )
            # Authentication, transport or provider failure must not trigger a paid loop.
            if response.status_code != 200 or not text or "本次处理失败" in text:
                break
        out = HOME / "plugin_data" / "companion_core" / "web-smoke-results.local.json"
        out.write_text(
            json.dumps(
                {"session_id": session, "results": results},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
