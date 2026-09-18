// ESP8266 D1 mini + MFRC522. SPI: D5 SCK, D6 MISO, D7 MOSI, D8 SDA/SS, D3 RST.
// Serial protocol: scan/registration plus admin Type 2 tag inspection and bounded NDEF URL writes.
#include <Arduino.h>
#include <SPI.h>
// Slow the SPI link for RC522-compatible boards that are sensitive to wiring.
#define MFRC522_SPICLOCK (1000000u)
#include <MFRC522.h>

MFRC522 reader(D8, D3);
String inputLine;
bool inputOverflow = false;
String armedToken;
String tagToken;
String tagAction;
String targetUid;
String tagDataHex;
bool present = false;
bool waitingForRelease = false;
unsigned long absentSince = 0;
String lastUid;
unsigned long wakeOk = 0;
unsigned long wakeTimeout = 0;
unsigned long wakeError = 0;
unsigned long selectOk = 0;
unsigned long selectError = 0;
byte lastWakeError = 255;
byte lastSelectError = 255;
byte lastWakeLength = 0;
byte lastReaderError = 0;
byte lastCollision = 0;
unsigned long lastRfReport = 0;

const byte MAX_USER_BYTES = 128;

void printHex(const byte *data, byte length) {
  const char alphabet[] = "0123456789ABCDEF";
  for (byte i = 0; i < length; ++i) {
    Serial.print(alphabet[data[i] >> 4]);
    Serial.print(alphabet[data[i] & 15]);
  }
}

int hexNibble(char value) {
  if (value >= '0' && value <= '9') return value - '0';
  if (value >= 'A' && value <= 'F') return value - 'A' + 10;
  if (value >= 'a' && value <= 'f') return value - 'a' + 10;
  return -1;
}

bool isHex(const String &value) {
  for (unsigned int i = 0; i < value.length(); ++i) {
    if (hexNibble(value[i]) < 0) return false;
  }
  return true;
}

String field(const String &line, const char *name) {
  String key = String('"') + name + '"';
  int start = line.indexOf(key);
  if (start < 0) return "";
  int colon = line.indexOf(':', start + key.length());
  if (colon < 0) return "";
  int first = line.indexOf('"', colon + 1);
  if (first < 0) return "";
  int last = line.indexOf('"', first + 1);
  if (last < 0) return "";
  return line.substring(first + 1, last);
}

void clearTagJob() {
  tagToken = "";
  tagAction = "";
  targetUid = "";
  tagDataHex = "";
}

void tagError(const char *code) {
  Serial.print(F("{\"type\":\"tag_error\",\"token\":\""));
  Serial.print(tagToken);
  Serial.print(F("\",\"code\":\""));
  Serial.print(code);
  Serial.println(F("\"}"));
  clearTagJob();
}

void hello() {
  byte version = reader.PCD_ReadRegister(MFRC522::VersionReg);
  Serial.print(F("{\"type\":\"hello\",\"reader\":\"MFRC522\",\"version\":"));
  Serial.print(version);
  Serial.println('}');
}

void reportRfStatus() {
  if (millis() - lastRfReport < 1000) return;
  lastRfReport = millis();
  Serial.print(F("{\"type\":\"rf_status\",\"wake_ok\":"));
  Serial.print(wakeOk);
  Serial.print(F(",\"wake_timeout\":"));
  Serial.print(wakeTimeout);
  Serial.print(F(",\"wake_error\":"));
  Serial.print(wakeError);
  Serial.print(F(",\"select_ok\":"));
  Serial.print(selectOk);
  Serial.print(F(",\"select_error\":"));
  Serial.print(selectError);
  Serial.print(F(",\"last_wake_error\":"));
  Serial.print(lastWakeError);
  Serial.print(F(",\"last_select_error\":"));
  Serial.print(lastSelectError);
  Serial.print(F(",\"last_wake_length\":"));
  Serial.print(lastWakeLength);
  Serial.print(F(",\"last_reader_error\":"));
  Serial.print(lastReaderError);
  Serial.print(F(",\"last_collision\":"));
  Serial.print(lastCollision);
  Serial.println('}');
}

void command(const String &line) {
  String action = field(line, "command");
  if (action == "hello") {
    hello();
  } else if (action == "cancel") {
    armedToken = "";
  } else if (action == "arm") {
    String token = field(line, "token");
    armedToken = token.length() == 32 && isHex(token) ? token : "";
    // A card already on the reader must be removed before this arm can fire.
    waitingForRelease = present || !absentSince || millis() - absentSince < 600;
    if (present) absentSince = 0;
  } else if (action == "cancel_tag") {
    if (field(line, "token") == tagToken) clearTagJob();
  } else if (action == "inspect" || action == "write_ndef") {
    clearTagJob();
    tagToken = field(line, "token");
    if (tagToken.length() != 32 || !isHex(tagToken)) {
      clearTagJob();
      return;
    }
    tagAction = action;
    if (action == "write_ndef") {
      targetUid = field(line, "uid");
      targetUid.toUpperCase();
      tagDataHex = field(line, "data_hex");
      if (!isHex(targetUid) || (targetUid.length() != 8 && targetUid.length() != 14 && targetUid.length() != 20) ||
          tagDataHex.length() < 16 || tagDataHex.length() > MAX_USER_BYTES * 2 ||
          tagDataHex.length() % 2 || !isHex(tagDataHex)) {
        tagError("invalid_command");
      }
    }
  }
}

bool getTagVersion(byte version[8]) {
  byte request[3] = {0x60, 0, 0};
  if (reader.PCD_CalculateCRC(request, 1, &request[1]) != MFRC522::STATUS_OK) return false;
  byte answer[10];
  byte size = sizeof(answer);
  if (reader.PCD_TransceiveData(request, sizeof(request), answer, &size, nullptr, 0, true) != MFRC522::STATUS_OK || size != 10) return false;
  memcpy(version, answer, 8);
  return true;
}

void inspectCard(const String &uid, const byte atqa[2]) {
  byte version[8];
  byte ccBlock[18];
  byte ccLength = sizeof(ccBlock);
  bool haveCC = reader.MIFARE_Read(3, ccBlock, &ccLength) == MFRC522::STATUS_OK && ccLength == 18;
  byte memory[MAX_USER_BYTES];
  byte memoryLength = 0;
  const char *readError = nullptr;
  if (!haveCC) {
    readError = "read_failed";
  } else if (reader.uid.sak != 0) {
    readError = "not_type2";
  } else {
    byte capacity = ccBlock[0] == 0xE1 ? min((unsigned int)MAX_USER_BYTES, (unsigned int)ccBlock[2] * 8) : 16;
    for (byte offset = 0; offset < capacity; offset += 16) {
      byte block[18];
      byte blockLength = sizeof(block);
      if (reader.MIFARE_Read(4 + offset / 4, block, &blockLength) != MFRC522::STATUS_OK || blockLength != 18) {
        readError = "read_failed";
        break;
      }
      byte copyLength = min((unsigned int)16, (unsigned int)capacity - offset);
      memcpy(memory + offset, block, copyLength);
      memoryLength += copyLength;
    }
  }
  // Some compatible tags do not implement GET_VERSION; do memory reads first.
  bool haveVersion = getTagVersion(version);
  Serial.print(F("{\"type\":\"inspect\",\"token\":\""));
  Serial.print(tagToken);
  Serial.print(F("\",\"uid\":\""));
  Serial.print(uid);
  Serial.print(F("\",\"sak\":"));
  Serial.print(reader.uid.sak);
  Serial.print(F(",\"atqa\":\""));
  printHex(atqa, 2);
  Serial.print(F("\",\"tag_version\":"));
  if (haveVersion) {
    Serial.print('"');
    printHex(version, 8);
    Serial.print('"');
  } else {
    Serial.print(F("null"));
  }
  Serial.print(F(",\"cc\":"));
  if (haveCC) {
    Serial.print('"');
    printHex(ccBlock, 4);
    Serial.print('"');
  } else {
    Serial.print(F("null"));
  }
  Serial.print(F(",\"user_hex\":"));
  if (memoryLength) {
    Serial.print('"');
    printHex(memory, memoryLength);
    Serial.print('"');
  } else {
    Serial.print(F("null"));
  }
  Serial.print(F(",\"read_error\":"));
  if (readError) {
    Serial.print('"');
    Serial.print(readError);
    Serial.print('"');
  } else {
    Serial.print(F("null"));
  }
  Serial.println('}');
  clearTagJob();
}

void writeCard(const String &uid) {
  if (uid != targetUid) {
    tagError("wrong_tag");
    return;
  }
  byte data[MAX_USER_BYTES];
  byte length = tagDataHex.length() / 2;
  for (byte i = 0; i < length; ++i) {
    data[i] = (hexNibble(tagDataHex[i * 2]) << 4) | hexNibble(tagDataHex[i * 2 + 1]);
  }
  if (length % 4 || data[0] != 0x03 || data[2] != 0xD1 || data[3] != 1 || data[5] != 'U' ||
      data[6] != 0 || data[1] != data[4] + 4 || 2 + data[1] >= length || data[2 + data[1]] != 0xFE) {
    tagError("invalid_command");
    return;
  }
  byte ccBlock[18];
  byte ccLength = sizeof(ccBlock);
  if (reader.uid.sak != 0 || reader.MIFARE_Read(3, ccBlock, &ccLength) != MFRC522::STATUS_OK || ccLength != 18 ||
      ccBlock[0] != 0xE1 || (ccBlock[1] >> 4) != 1 || (ccBlock[3] & 0x0F) != 0 ||
      (unsigned int)ccBlock[2] * 8 < length) {
    tagError("unsupported");
    return;
  }
  // Mark NDEF empty until all later pages have been written; page 4 is committed last.
  byte emptyNdef[4] = {0x03, 0x00, 0xFE, 0x00};
  if (reader.MIFARE_Ultralight_Write(4, emptyNdef, 4) != MFRC522::STATUS_OK) {
    tagError("write_failed");
    return;
  }
  for (byte offset = 4; offset < length; offset += 4) {
    if (reader.MIFARE_Ultralight_Write(4 + offset / 4, data + offset, 4) != MFRC522::STATUS_OK) {
      tagError("write_failed");
      return;
    }
  }
  if (reader.MIFARE_Ultralight_Write(4, data, 4) != MFRC522::STATUS_OK) {
    tagError("write_failed");
    return;
  }
  for (byte offset = 0; offset < length; offset += 16) {
    byte block[18];
    byte blockLength = sizeof(block);
    if (reader.MIFARE_Read(4 + offset / 4, block, &blockLength) != MFRC522::STATUS_OK || blockLength != 18 ||
        memcmp(block, data + offset, min((unsigned int)16, (unsigned int)length - offset)) != 0) {
      tagError("verify_failed");
      return;
    }
  }
  Serial.print(F("{\"type\":\"write_ndef\",\"token\":\""));
  Serial.print(tagToken);
  Serial.print(F("\",\"uid\":\""));
  Serial.print(uid);
  Serial.println(F("\",\"verified\":true}"));
  clearTagJob();
}

void setup() {
  Serial.begin(115200);
  SPI.begin();
  reader.PCD_Init();
  // Use the default 33 dB gain; maximum gain causes receive errors with nearby tags.
  reader.PCD_SetAntennaGain(MFRC522::RxGain_avg);
  delay(200);
  hello();
}

void loop() {
  while (Serial.available()) {
    char value = Serial.read();
    if (value == '\n') {
      if (!inputOverflow) command(inputLine);
      inputLine = "";
      inputOverflow = false;
    } else if (!inputOverflow && value != '\r') {
      if (inputLine.length() < 450) inputLine += value;
      else inputOverflow = true;
    }
  }

  byte atqa[2];
  byte atqaSize = sizeof(atqa);
  MFRC522::StatusCode status = reader.PICC_RequestA(atqa, &atqaSize);
  if (status != MFRC522::STATUS_OK) {
    atqaSize = sizeof(atqa);
    status = reader.PICC_WakeupA(atqa, &atqaSize);
  }
  if (status == MFRC522::STATUS_OK) {
    wakeOk++;
    status = reader.PICC_Select(&reader.uid);
    if (status == MFRC522::STATUS_OK) selectOk++;
    else {
      selectError++;
      lastSelectError = status;
    }
  } else if (status == MFRC522::STATUS_TIMEOUT) {
    wakeTimeout++;
  } else {
    wakeError++;
    lastWakeError = status;
    lastWakeLength = atqaSize;
    lastReaderError = reader.PCD_ReadRegister(MFRC522::ErrorReg);
    lastCollision = reader.PCD_ReadRegister(MFRC522::CollReg);
  }
  reportRfStatus();
  if (status != MFRC522::STATUS_OK) {
    if (!absentSince) absentSince = millis();
    if (millis() - absentSince >= 600) {
      present = false;
      waitingForRelease = false;
    }
    delay(50);
    return;
  }

  String uid;
  for (byte i = 0; i < reader.uid.size; ++i) {
    if (reader.uid.uidByte[i] < 16) uid += '0';
    uid += String(reader.uid.uidByte[i], HEX);
  }
  uid.toUpperCase();
  bool newPresentation = !present || uid != lastUid;
  present = true;
  absentSince = 0;
  lastUid = uid;

  if (tagAction == "inspect") inspectCard(uid, atqa);
  else if (tagAction == "write_ndef") writeCard(uid);

  if (newPresentation && !waitingForRelease) {
    Serial.print(F("{\"type\":\"scan\",\"uid\":\""));
    Serial.print(uid);
    Serial.print('"');
    if (armedToken.length()) {
      Serial.print(F(",\"arm\":\""));
      Serial.print(armedToken);
      Serial.print('"');
      armedToken = "";
    }
    Serial.println('}');
  }
  reader.PICC_HaltA();
  reader.PCD_StopCrypto1();
  delay(50);
}
