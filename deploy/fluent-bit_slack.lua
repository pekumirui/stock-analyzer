-- Keep in sync with deploy/alert_patterns.py slack_payload.
-- Incoming Webhook JSON: JST clock + hex-red attachment bar (#E01E5A).

local JST_OFFSET = 9 * 3600
local HEADER_MAX = 150
local SECTION_MAX = 2900
local FALLBACK_MAX = 2000

local function epoch_sec(ts)
  if type(ts) == "table" then
    return math.floor(tonumber(ts.sec or ts[1]) or os.time())
  end
  return math.floor(tonumber(ts) or os.time())
end

local function clip(text, limit)
  if #text <= limit then
    return text
  end
  return string.sub(text, 1, limit - 3) .. "…"
end

local function json_esc(s)
  s = string.gsub(s, "\\", "\\\\")
  s = string.gsub(s, '"', '\\"')
  s = string.gsub(s, "\n", "\\n")
  s = string.gsub(s, "\r", "\\r")
  s = string.gsub(s, "\t", "\\t")
  return s
end

local function format_jst(sec)
  return os.date("!%Y-%m-%d %H:%M:%S JST", sec + JST_OFFSET)
end

function format_slack(tag, timestamp, record)
  local source = record["source"]
  if type(source) ~= "string" or source == "" then
    source = "log"
  end
  local line = record["log"]
  if type(line) ~= "string" or line == "" then
    line = record["MESSAGE"]
  end
  if type(line) ~= "string" then
    line = ""
  end
  line = string.gsub(line, "\r\n", "\n")
  line = string.gsub(line, "\n+$", "")
  line = string.gsub(line, "```", "'''")

  local sec = epoch_sec(timestamp)
  local jst = format_jst(sec)
  local header = clip("error · " .. source, HEADER_MAX)
  local body = clip("*" .. jst .. "*\n```\n" .. line .. "\n```", SECTION_MAX)
  local fallback = clip("[" .. source .. "] " .. jst .. ": " .. line, FALLBACK_MAX)

  -- color + blocks only. fallback/ts/title here → Slack 400 invalid_attachments.
  local json = string.format(
    '{"text":"%s","attachments":[{"color":"#E01E5A","blocks":[{"type":"header","text":{"type":"plain_text","text":"%s","emoji":true}},{"type":"section","text":{"type":"mrkdwn","text":"%s"}}]}]}',
    json_esc(fallback),
    json_esc(header),
    json_esc(body)
  )
  return 1, timestamp, {
    body = json,
    headers = { ["Content-Type"] = "application/json" },
  }
end
