/**
 * HK Macro MPF for Fusion 360
 * Implements the HK mapping in samples/schema.txt
 * - HKOST registration is built from the HKSTR start point (anchor requirement)
 * - HKSTR encodes start + lead target, HKCUT precedes the first cutting move
 * - Technology numbers come from explicit lookup (material + thickness + opType)
 */

description = "HK Macro MPF (Fusion 360)";
vendor = "GNC / HK Laser";
vendorUrl = "http://www.gnci.org";
legal = "Internal use. Validate and tune HK macro parameters per machine.";
certificationLevel = 2;
longDescription = "Fusion 360 post that emits HK macros (HKLDB/HKINI/HKOST/HKSTR/HKPIE/HKLEA/HKCUT/HKSTO/HKPED).";

capabilities = CAPABILITY_JET;
extension = "mpf";
mimetype = "application/mpf";
setCodePage("utf-8");

minimumCircularSweep = toRad(0.01);
maximumCircularSweep = toRad(180);
allowHelicalMoves = false;
allowedCircularPlanes = (1 << PLANE_XY);

var properties = {
  materialLibrary: { title: "Material library (HKLDB arg1)", type: "integer", value: 2, default: 2 },
  materialName: { title: "Material name (HKLDB string)", type: "string", value: "S304", default: "S304" },
  processClass: { title: "Process class (HKLDB arg3)", type: "integer", value: 3, default: 3 },
  initMode: { title: "HKINI mode", type: "integer", value: 15, default: 15 },
  sheetX: { title: "Sheet X (HKINI)", type: "number", value: 118.3, default: 118.3 },
  sheetY: { title: "Sheet Y (HKINI)", type: "number", value: 13.9, default: 13.9 },
  deriveSheetFromStock: {
    title: "Use setup stock for HKINI",
    type: "boolean",
    value: true,
    default: true
  },
  stockMargin: { title: "Margin added to stock X/Y (same units as output)", type: "number", value: 0, default: 0 },
  sheetThickness: { title: "Sheet thickness (mm)", type: "number", value: 1.5, default: 1.5 },
  deriveThicknessFromStock: {
    title: "Use setup stock Z for tech lookup",
    type: "boolean",
    value: true,
    default: true
  },
  forceInchOutput: {
    title: "Force inch output",
    description: "Converts mm → inches when Fusion setup is metric.",
    type: "boolean",
    value: true,
    default: true
  },
  hkOstAngleDefault: { title: "HKOST angle default (deg)", type: "number", value: 0.0, default: 0.0 },
  technologyTable: {
    title: "Technology map JSON (material → thickness → opType → tech)",
    type: "string",
    value: '{"S304":{"default":{"contour":5,"slot":3,"pierce-only":9},"1.5mm":{"contour":5,"slot":3,"pierce-only":9}}}',
    default: '{"S304":{"default":{"contour":5,"slot":3,"pierce-only":9},"1.5mm":{"contour":5,"slot":3,"pierce-only":9}}}'
  },
  defaultTechNumber: {
    title: "Fallback tech number (used only when no map/override exists)",
    type: "integer",
    value: 0,
    default: 0
  },
  kerfMode: {
    title: "Kerf mode (none/compensated)",
    type: "string",
    value: "compensated",
    default: "compensated"
  },
  whenPlacement: {
    title: "WHEN placement (beforeLastCut/end)",
    type: "string",
    value: "beforeLastCut",
    default: "beforeLastCut"
  },
  emitHKPEDAtEnd: { title: "Emit HKPED(0,0,0) at end of each section", type: "boolean", value: true, default: true }
};

var operationProperties = {
  hkForceType: { title: "HK contour type (AUTO/INNER/OUTER/CHAIN)", type: "string", value: "AUTO", default: "AUTO" },
  hkTech: { title: "HK tech override", type: "number", value: 0, default: 0 },
  hkKerfMode: { title: "Kerf mode override (none/compensated)", type: "string", value: "", default: "" },
  hkOpType: { title: "Cut type (contour/slot/pierce-only)", type: "string", value: "", default: "" }
};

// Formats
var xyzFormat = createFormat({ decimals: 4, forceDecimal: true });
var ijFormat = createFormat({ decimals: 4, forceDecimal: true });
var xOutput = createVariable({ prefix: "X" }, xyzFormat);
var yOutput = createVariable({ prefix: "Y" }, xyzFormat);
var iOutput = createVariable({ prefix: "I" }, ijFormat);
var jOutput = createVariable({ prefix: "J" }, ijFormat);
function fmt(x) { return xyzFormat.format(x); }

// Output helpers
function writeBlock() {
  if (arguments.length > 0) {
    writeWords(arguments);
  }
}

// Units
function toOutUnits(v) {
  var forceInch = properties.forceInchOutput && (properties.forceInchOutput.value !== undefined ? properties.forceInchOutput.value : properties.forceInchOutput);
  if (!forceInch) return v;
  if (unit == MM) return v / 25.4;
  return v;
}

// Helpers
function getSectionOverride(propName, defaultValue) {
  try {
    if (currentSection && currentSection.properties && currentSection.properties[propName] !== undefined) {
      return currentSection.properties[propName];
    }
  } catch (e) {}
  return defaultValue;
}

function parseTechTable() {
  var raw = properties.technologyTable.value || properties.technologyTable || "";
  if (!raw) return {};
  try {
    return JSON.parse(String(raw));
  } catch (e) {
    warning("Invalid technologyTable JSON; falling back to defaults/overrides.");
    return {};
  }
}

function formatThicknessKey(thicknessMM) {
  if (!isFinite(thicknessMM) || thicknessMM <= 0) return "default";
  var rounded = Math.round(thicknessMM * 10) / 10;
  return rounded.toFixed(1) + "mm";
}

function resolveStockSize() {
  var sizeX = toOutUnits(properties.sheetX.value);
  var sizeY = toOutUnits(properties.sheetY.value);
  if (properties.deriveSheetFromStock.value && typeof getWorkpiece === "function") {
    var wp = getWorkpiece();
    if (wp && wp.upper && wp.lower) {
      sizeX = toOutUnits(wp.upper.x - wp.lower.x);
      sizeY = toOutUnits(wp.upper.y - wp.lower.y);
    }
  }
  var margin = Number(properties.stockMargin.value) || 0;
  return { x: sizeX + margin * 2, y: sizeY + margin * 2 };
}

function resolveThicknessMM() {
  var thickness = properties.sheetThickness.value;
  if (properties.deriveThicknessFromStock.value && typeof getWorkpiece === "function") {
    var wp = getWorkpiece();
    if (wp && wp.upper && wp.lower) {
      thickness = (wp.upper.z - wp.lower.z) * (unit == MM ? 1 : 25.4);
    }
  }
  return thickness;
}

function normalizeKerfMode(mode) {
  var m = (mode || "").toString().toLowerCase().trim();
  if (m === "none") return 0;
  if (m === "compensated") return 1;
  return 1;
}

function isLeadMovement(mv) {
  return mv === MOVEMENT_LEAD_IN ||
         mv === MOVEMENT_LEAD_OUT ||
         mv === MOVEMENT_PIERCE ||
         mv === MOVEMENT_PIERCE_LINEAR ||
         mv === MOVEMENT_PIERCE_CIRCULAR ||
         mv === MOVEMENT_RAMP ||
         mv === MOVEMENT_RAPID ||
         mv === MOVEMENT_LINK_TRANSITION ||
         mv === MOVEMENT_LINK_DIRECT;
}

function getParameterValue(name) {
  if (typeof hasParameter === "function" && hasParameter(name)) {
    return getParameter(name);
  }
  return "";
}

function determineOperationType() {
  var override = getSectionOverride("hkOpType", "");
  if (override && override.toString().trim().length > 0) {
    return override.toString().toLowerCase();
  }

  var strategy = String(getParameterValue("operation-strategy") || getParameterValue("operation:strategy") || "").toLowerCase();
  var comment = String(getParameterValue("operation-comment") || "").toLowerCase();
  var combined = strategy + " " + comment;

  if (combined.indexOf("slot") !== -1) return "slot";
  if (combined.indexOf("pierce") !== -1 || combined.indexOf("etch") !== -1 || combined.indexOf("mark") !== -1) return "pierce-only";
  return "contour";
}

function resolveTech() {
  var override = Number(getSectionOverride("hkTech", 0)) || 0;
  if (override > 0) return override;

  var material = (properties.materialName.value || properties.materialName).toString();
  var thicknessKey = formatThicknessKey(resolveThicknessMM());
  var opType = determineOperationType();
  var table = parseTechTable();

  if (table[material]) {
    var techByThickness = table[material][thicknessKey] || table[material]["default"];
    if (techByThickness && techByThickness[opType] !== undefined) {
      return Number(techByThickness[opType]);
    }
  }

  var fallback = Number(properties.defaultTechNumber.value || properties.defaultTechNumber || 0);
  if (fallback > 0) {
    warning("Using defaultTechNumber because no tech mapping matched material=" + material + " thickness=" + thicknessKey + " opType=" + opType);
    return fallback;
  }

  error("No technology mapping found and defaultTechNumber is 0. Set hkTech on the operation or provide a mapping.");
  return 0;
}

function signedArea(points) {
  var a = 0;
  for (var i = 0; i < points.length - 1; i++) {
    a += points[i].x * points[i + 1].y - points[i + 1].x * points[i].y;
  }
  return a / 2.0;
}

function closeEnough(a, b, eps) { return Math.abs(a - b) <= eps; }

function isClosedContour(points) {
  if (!points || points.length < 3) return false;
  var eps = 0.0008;
  var p0 = points[0], pn = points[points.length - 1];
  return closeEnough(p0.x, pn.x, eps) && closeEnough(p0.y, pn.y, eps);
}

function resolveHKTypeFlag(points, forceType) {
  var ft = (forceType || "AUTO").toString().trim().toUpperCase();
  if (ft === "INNER") return 1;
  if (ft === "OUTER" || ft === "CHAIN") return 0;
  var closed = isClosedContour(points);
  if (!closed) return 0;
  return signedArea(points) < 0 ? 1 : 0;
}

function renderMotion(m) {
  var parts = [m.cmd];
  if (m.x !== undefined) parts.push(xOutput.format(m.x));
  if (m.y !== undefined) parts.push(yOutput.format(m.y));
  if (m.i !== undefined) parts.push(iOutput.format(m.i));
  if (m.j !== undefined) parts.push(jOutput.format(m.j));
  return parts.join(" ");
}

// State
var sectionCount = 0;
var sectionRecords = [];
var cur = null;

function onOpen() {
  sectionCount = 0;
  sectionRecords = [];
  cur = null;
}

function onSection() {
  sectionCount++;
  var baseN = sectionCount * 10000;
  var opId = baseN + 1;

  cur = {
    index: sectionCount,
    baseN: baseN,
    opId: opId,
    tech: resolveTech(),
    angle: Number(properties.hkOstAngleDefault.value || properties.hkOstAngleDefault || 0) || 0,
    motions: [],
    start: null,
    leadTarget: null,
    typeOverride: getSectionOverride("hkForceType", "AUTO"),
    kerfOverride: getSectionOverride("hkKerfMode", ""),
    opTypeOverride: getSectionOverride("hkOpType", ""),
    firstCutIndex: -1
  };
}

function capturePointAndStart(x, y) {
  if (!cur) return;
  if (cur.start === null && x !== undefined && y !== undefined) {
    cur.start = { x: x, y: y };
  }
  if (cur.leadTarget === null && cur.start !== null && x !== undefined && y !== undefined) {
    if (!closeEnough(cur.start.x, x, 1e-7) || !closeEnough(cur.start.y, y, 1e-7)) {
      cur.leadTarget = { x: x, y: y };
    }
  }
}

function pushMotion(cmd, x, y, i, j, isCutting) {
  if (!cur) return;
  capturePointAndStart(x, y);
  var motion = { cmd: cmd, x: x, y: y, i: i, j: j, isCutting: isCutting };
  if (isCutting && cur.firstCutIndex === -1) {
    cur.firstCutIndex = cur.motions.length;
  }
  cur.motions.push(motion);
}

function onLinear(x, y, z, feed) {
  if (!cur) return;
  var X = (x !== undefined) ? toOutUnits(x) : undefined;
  var Y = (y !== undefined) ? toOutUnits(y) : undefined;
  var isCutting = !isLeadMovement(movement);
  pushMotion("G1", X, Y, undefined, undefined, isCutting);
}

function onCircular(clockwise, cx, cy, cz, x, y, z, feed) {
  if (!cur) return;
  var X = (x !== undefined) ? toOutUnits(x) : undefined;
  var Y = (y !== undefined) ? toOutUnits(y) : undefined;
  if (X === undefined || Y === undefined) return;

  var start = getCurrentPosition();
  var sx = toOutUnits(start.x);
  var sy = toOutUnits(start.y);
  var I = toOutUnits(cx) - sx;
  var J = toOutUnits(cy) - sy;
  var isCutting = !isLeadMovement(movement);
  pushMotion(clockwise ? "G2" : "G3", X, Y, I, J, isCutting);
}

function onSectionEnd() {
  if (!cur) return;
  if (!cur.start) {
    cur = null;
    return;
  }

  var leadTarget = cur.leadTarget || cur.start;
  var kerf = normalizeKerfMode(cur.kerfOverride || properties.kerfMode.value || properties.kerfMode);

  var points = [];
  points.push(cur.start);
  for (var p = 0; p < cur.motions.length; p++) {
    if (cur.motions[p].x !== undefined && cur.motions[p].y !== undefined) {
      points.push({ x: cur.motions[p].x, y: cur.motions[p].y });
    }
  }
  var typeFlag = resolveHKTypeFlag(points, cur.typeOverride);

  var blockLines = [];
  blockLines.push("N" + cur.opId + " HKSTR(" + typeFlag + "," + kerf + "," + fmt(cur.start.x) + "," + fmt(cur.start.y) + ",0," + fmt(leadTarget.x) + "," + fmt(leadTarget.y) + ",0)");
  blockLines.push("HKPIE(0,0,0)");
  blockLines.push("HKLEA(0,0,0)");

  var cutStart = cur.firstCutIndex >= 0 ? cur.firstCutIndex : cur.motions.length;
  for (var i = 0; i < cutStart; i++) {
    blockLines.push(renderMotion(cur.motions[i]));
  }

  var whenPlacement = (properties.whenPlacement.value || properties.whenPlacement || "beforeLastCut").toString().toLowerCase();

  if (cutStart < cur.motions.length) {
    blockLines.push("HKCUT(0,0,0)");
    for (var j = cutStart; j < cur.motions.length; j++) {
      if (j === cur.motions.length - 1 && whenPlacement === "beforelastcut") {
        blockLines.push("WHEN ($AC_TIME>0.005)AND($R71<$R72)AND($R3==1)AND($R60==1) DO $A_DBB[10]=1");
      }
      blockLines.push(renderMotion(cur.motions[j]));
    }
    if (whenPlacement === "end") {
      blockLines.push("WHEN ($AC_TIME>0.005)AND($R71<$R72)AND($R3==1)AND($R60==1) DO $A_DBB[10]=1");
    }
  }

  blockLines.push("HKSTO(0,0,0)");
  if (properties.emitHKPEDAtEnd.value) {
    blockLines.push("HKPED(0,0,0)");
  }

  sectionRecords.push({
    baseN: cur.baseN,
    opId: cur.opId,
    tech: cur.tech,
    angle: cur.angle,
    anchor: cur.start,
    lines: blockLines
  });

  cur = null;
}

function onClose() {
  writeBlock("N1");
  writeBlock(
    "HKLDB(" +
    properties.materialLibrary.value + ',"' +
    properties.materialName.value + '",' +
    properties.processClass.value +
    ",0,0,0)"
  );

  var stock = resolveStockSize();
  writeBlock(
    "HKINI(" +
    properties.initMode.value + "," +
    fmt(stock.x) + "," +
    fmt(stock.y) +
    ",0,0,0)"
  );

  for (var i = 0; i < sectionRecords.length; i++) {
    var rec = sectionRecords[i];
    writeBlock("N" + rec.baseN + " HKOST(" + fmt(rec.anchor.x) + "," + fmt(rec.anchor.y) + "," + fmt(rec.angle) + "," + rec.opId + "," + rec.tech + ",0,0,0)");
    writeBlock("HKPPP");
  }

  for (var b = 0; b < sectionRecords.length; b++) {
    var lines = sectionRecords[b].lines;
    for (var l = 0; l < lines.length; l++) {
      writeBlock(lines[l]);
    }
  }

  var endN = (sectionRecords.length + 1) * 100000;
  writeBlock("N" + endN + " HKEND(0,0,0)");
  writeBlock("N10 M30");
}
