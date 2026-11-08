#include "setup_portal.h"

#include <DNSServer.h>
#include <M5Unified.h>
#include <WebServer.h>
#include <WiFi.h>

namespace {
const uint32_t kPortalTimeoutMs = 10 * 60 * 1000;

String randomPassword() {
  const char* alphabet = "abcdefghjkmnpqrstuvwxyz23456789";  // no look-alikes
  String password;
  for (int i = 0; i < 10; ++i) password += alphabet[esp_random() % strlen(alphabet)];
  return password;
}

String htmlEscape(const String& text) {
  String escaped;
  for (size_t i = 0; i < text.length(); ++i) {
    const char c = text[i];
    if (c == '&') escaped += "&amp;";
    else if (c == '<') escaped += "&lt;";
    else if (c == '>') escaped += "&gt;";
    else if (c == '"') escaped += "&quot;";
    else escaped += c;
  }
  return escaped;
}

String hexColor(uint32_t rgb) {
  char text[8];
  snprintf(text, sizeof(text), "%06lX", static_cast<unsigned long>(rgb & 0xFFFFFF));
  return String(text);
}

String settingsForm(const BodySettings& settings) {
  String html =
      "<!doctype html><html lang=ja><meta charset=utf-8>"
      "<meta name=viewport content='width=device-width,initial-scale=1'>"
      "<title>体の設定</title><style>"
      "body{font-family:sans-serif;margin:16px;max-width:520px;color:#223}"
      "label{display:block;margin-top:12px;font-size:14px;color:#556}"
      "input{width:100%;padding:8px;font-size:16px;box-sizing:border-box}"
      "fieldset{border:1px solid #ccd;border-radius:8px;margin-top:14px}"
      "button{margin-top:20px;width:100%;padding:12px;font-size:17px}</style>"
      "<h1>体の設定</h1><form method=post action=/save>";
  for (int slot = 0; slot < BodySettings::kWifiSlotCount; ++slot) {
    const String n = String(slot + 1);
    html += "<fieldset><legend>Wi-Fi " + n + (slot == 0 ? "（家）" : "（任意）") + "</legend>";
    html += "<label>名前（SSID）<input name=ssid" + n + " value=\"" +
            htmlEscape(settings.wifi_ssid[slot]) + "\"></label>";
    html += "<label>パスワード（空のままなら今のを使う）<input type=password name=pass" + n + "></label>";
    html += "</fieldset>";
  }
  html += "<label>頭の住所（ws:// か wss://）<input name=url placeholder='ws://192.168.3.17:8790/body' value=\"" +
          htmlEscape(settings.link_url) + "\"></label>";
  html += "<label>合言葉（空のままなら今のを使う）<input type=password name=token></label>";
  html += "<label>体の名前<input name=name value=\"" + htmlEscape(settings.body_name) + "\"></label>";
  html += "<label>顔の色（RRGGBB）<input name=color value=\"" + hexColor(settings.face_rgb) + "\"></label>";
  html += "<button>保存して再起動</button></form></html>";
  return html;
}

void applyForm(WebServer& server, BodySettings& settings) {
  for (int slot = 0; slot < BodySettings::kWifiSlotCount; ++slot) {
    const String n = String(slot + 1);
    const String ssid = server.arg("ssid" + n);
    const String password = server.arg("pass" + n);
    if (ssid != settings.wifi_ssid[slot]) settings.wifi_password[slot] = "";
    settings.wifi_ssid[slot] = ssid;
    if (password.length() > 0) settings.wifi_password[slot] = password;
  }
  settings.link_url = server.arg("url");
  if (server.arg("token").length() > 0) settings.link_token = server.arg("token");
  if (server.arg("name").length() > 0) settings.body_name = server.arg("name");
  if (server.arg("color").length() == 6) settings.face_rgb = strtoul(server.arg("color").c_str(), nullptr, 16);
}

void drawPortalScreen(const String& ap_name, const String& ap_password) {
  M5.Display.fillScreen(TFT_WHITE);
  const String qr_text = "WIFI:T:WPA;S:" + ap_name + ";P:" + ap_password + ";;";
  M5.Display.qrcode(qr_text, 8, 40, 150, 3);

  M5.Display.setTextColor(TFT_BLACK, TFT_WHITE);
  M5.Display.setFont(&fonts::lgfxJapanGothic_16);
  M5.Display.drawString("体の設定", 8, 10);
  M5.Display.setFont(&fonts::lgfxJapanGothic_12);
  int y = 46;
  for (const char* line : {"1. スマホで QR を読む", "2. 開いたページで入力", "", "開かないときは", "http://192.168.4.1"}) {
    M5.Display.drawString(line, 168, y);
    y += 20;
  }
  M5.Display.drawString(("Wi-Fi: " + ap_name).c_str(), 168, y + 10);
  M5.Display.drawString(("pass: " + ap_password).c_str(), 168, y + 30);
}

void showMessageAndRestart(const char* message) {
  M5.Display.fillScreen(TFT_WHITE);
  M5.Display.setTextColor(TFT_BLACK, TFT_WHITE);
  M5.Display.setFont(&fonts::lgfxJapanGothic_20);
  M5.Display.drawString(message, 20, 110);
  delay(1500);
  ESP.restart();
  while (true) delay(1000);
}

void printSettings(const BodySettings& settings) {
  for (int slot = 0; slot < BodySettings::kWifiSlotCount; ++slot) {
    Serial.printf("wifi %d: %s%s\n", slot + 1, settings.wifi_ssid[slot].c_str(),
                  settings.wifi_password[slot].length() > 0 ? " (password set)" : "");
  }
  Serial.printf("url: %s\ntoken: %s\nname: %s\ncolor: %s\nvolume: %u\n",
                settings.link_url.c_str(), settings.link_token.length() > 0 ? "(set)" : "(empty)",
                settings.body_name.c_str(), hexColor(settings.face_rgb).c_str(), settings.volume_percent);
}

// "wifi 2 MySSID secret" -> command "wifi", argument "2 MySSID secret"
void splitCommand(const String& line, String& command, String& argument) {
  const int space = line.indexOf(' ');
  command = space < 0 ? line : line.substring(0, space);
  argument = space < 0 ? "" : line.substring(space + 1);
  argument.trim();
}

bool applyWifiCommand(BodySettings& settings, const String& argument) {
  const int first_space = argument.indexOf(' ');
  const int slot = argument.substring(0, first_space < 0 ? argument.length() : first_space).toInt() - 1;
  if (slot < 0 || slot >= BodySettings::kWifiSlotCount) return false;

  const String rest = first_space < 0 ? "" : argument.substring(first_space + 1);
  const int second_space = rest.indexOf(' ');
  settings.wifi_ssid[slot] = second_space < 0 ? rest : rest.substring(0, second_space);
  settings.wifi_password[slot] = second_space < 0 ? "" : rest.substring(second_space + 1);
  return true;
}
}  // namespace

void runSetupPortal(BodySettings& settings) {
  const String mac_suffix = WiFi.macAddress().substring(12);  // "AB:CD"
  String ap_name = "body-setup-" + mac_suffix;
  ap_name.replace(":", "");
  const String ap_password = randomPassword();

  WiFi.mode(WIFI_AP);
  WiFi.softAP(ap_name.c_str(), ap_password.c_str());
  DNSServer dns;
  dns.start(53, "*", WiFi.softAPIP());  // every name -> us, so the phone pops the page
  WebServer server(80);
  server.on("/save", HTTP_POST, [&]() {
    applyForm(server, settings);
    settings.save();
    server.send(200, "text/html; charset=utf-8",
                "<meta charset=utf-8><p>保存しました。体が再起動します。</p>");
    delay(500);
    showMessageAndRestart("保存した。再起動します");
  });
  server.onNotFound([&]() { server.send(200, "text/html; charset=utf-8", settingsForm(settings)); });
  server.begin();

  drawPortalScreen(ap_name, ap_password);
  Serial.printf("[setup] portal open: %s / %s\n", ap_name.c_str(), ap_password.c_str());

  const uint32_t started_ms = millis();
  while (millis() - started_ms < kPortalTimeoutMs) {
    dns.processNextRequest();
    server.handleClient();
    handleSerialCommands(settings);
    delay(5);
  }
  showMessageAndRestart("時間切れ。再起動します");
  while (true) delay(1000);
}

bool handleSerialCommands(BodySettings& settings) {
  static String line;
  while (Serial.available() > 0) {
    const char c = static_cast<char>(Serial.read());
    if (c != '\n' && c != '\r') {
      line += c;
      continue;
    }
    if (line.length() == 0) continue;

    String command, argument;
    splitCommand(line, command, argument);
    line = "";

    bool changed = true;
    if (command == "wifi") changed = applyWifiCommand(settings, argument);
    else if (command == "url") settings.link_url = argument;
    else if (command == "token") settings.link_token = argument;
    else if (command == "name") settings.body_name = argument;
    else if (command == "color") settings.face_rgb = strtoul(argument.c_str(), nullptr, 16);
    else if (command == "volume") settings.volume_percent = constrain(argument.toInt(), 0, 100);
    else {
      changed = false;
      if (command == "show") printSettings(settings);
      else if (command == "reboot") ESP.restart();
      else if (command == "portal") runSetupPortal(settings);
      else Serial.println("commands: show | wifi <1-3> <ssid> [password] | url <ws-url> | token <t> | "
                          "name <n> | color <rrggbb> | volume <0-100> | portal | reboot");
    }

    if (changed) {
      settings.save();
      Serial.println("saved. `reboot` to apply.");
      return true;
    }
  }
  return false;
}
