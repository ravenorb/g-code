/**
 * HK Macro MPF for Fusion 360 - FINAL WORKING VERSION FOR ENGINE 5.306.0
 */

description = "HK Macro MPF (Fusion 360) - GNC";
vendor = "GNC / HK Laser";
vendorUrl = "http://www.gnci.org";
legal = "Internal use. Validate and tune HK macro parameters per machine.";
certificationLevel = 2;
longDescription = "This post is for hk Fiber laser";

capabilities = CAPABILITY_JET;
extension = "mpf";
mimetype = "application/mpf";
setCodePage("utf-8");

minimumCircularSweep = toRad(0.01);
maximumCircularSweep = toRad(180);
allowHelicalMoves = false;
allowedCircularPlanes = (1 << PLANE_XY);

var properties = {
  hkLdbA: { title: "HKLDB arg1", type: "number", value: 2, default: 2 },
  hkLdbStr: { title: "HKLDB string", type: "string", value: "5304", default: "S304" },
  hkLdbB: { title: "HKLDB arg3", type: "number", value: 3, default: 3 },
  hkIniA: { title: "HKINI arg1", type: "number", value: 76, default: 76 },
  hkIniX: { title: "HKINI X", type: "number", value: 118.7, default: 118.7 },
  hkIniY: { title: "HKINI Y", type: "number", value: 59.5, default: 59.5 },
  forceInchOutput: {
    title: "Force inch output",
    description: "Your samples look inch-based. If your Fusion setup is metric, this converts MM->IN.",
    type: "boolean",
    value: true,
    default: true
  },
  hkOstAngleDefault: { title: "HKOST angle default (deg)", type: "number", value: 0.0, default: 0.0 },
  whenPlacement: {
    title: "WHEN placement",
    description: "beforeLastMove (matches samples) or end (right before HKSTO)",
    type: "string",
    value: "beforeLastMove",
    default: "beforeLastMove"
  },
  emitHKPEDAtEnd: { title: "Emit HKPED(0,0,0) at end", type: "boolean", value: true, default: true }
};

var operationProperties = {
  hkForceType: { title: "HK contour type (AUTO/INNER/OUTER/CHAIN)", type: "string", value: "AUTO", default: "AUTO" },
  hkTech: { title: "HKOST tech override (0 = auto map)", type: "number", value: 0, default: 0 }
};

// Formats
var xyzFormat = createFormat({ decimals: 4, forceDecimal: true });
var ijFormat = createFormat({ decimals: 4, forceDecimal: true });
var xOutput = createVariable({ prefix: "X" }, xyzFormat);
var yOutput = createVariable({ prefix: "Y" }, xyzFormat);
var iOutput = createVariable({ prefix: "I" }, ijFormat);
var jOutput = createVariable({ prefix: "J" }, ijFormat);
function fmt(x) { return xyzFormat.format(x); }

// Units
function toOutUnits(v) {
  if (!properties.forceInchOutput) return v;
  if (unit == MM) return v / 25.4;
  return v;
}

// State
var sectionCount = 0;
var ostLines = [];
var blocks = [];
var cur = null;

// Geometry helpers - DEFINED BEFORE USE
function signedArea(points) {
  var a = 0;
  for (var i = 0; i < points.length - 1; i++) {
    a += points[i].x * points[i+1].y - points[i+1].x * points[i].y;
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
  var ft = (forceType || "AUTO").trim().toUpperCase();
  if (ft === "INNER") return 1;
  if (ft === "OUTER") return 0;
  if (ft === "CHAIN") return 0;
  var closed = isClosedContour(points);
  if (!closed) return 0;
  var a = signedArea(points);
  if (a < 0) return 1;
  return 0;
}

function writeBlock() {
  if (arguments.length > 0) {
    writeWords(arguments);
  }
}

// Override and tech - DEFINED BEFORE USE
function getSectionOverride(propName, defaultValue) {
  try {
    if (currentSection && currentSection.properties && currentSection.properties[propName] !== undefined) {
      return currentSection.properties[propName];
    }
  } catch (e) {}
  return defaultValue;
}

function resolveTech() {
  var techOverride = Number(getSectionOverride("hkTech", 0)) || 0;
  if (techOverride > 0) return techOverride;
  return 5;
}

// -----------------------------------------------------------------------------
// NO onOpen() — framework default initializes writeBlock
// -----------------------------------------------------------------------------

function onSection() {
  sectionCount++;

  var listN = sectionCount * 10000;
  var subId = listN + 1;
  var init = currentSection.getInitialPosition();
  var pierceX = toOutUnits(init.x);
  var pierceY = toOutUnits(init.y);
  var tech = resolveTech();
  var ang = Number(properties.hkOstAngleDefault) || 0;

  ostLines.push("N" + listN + " HKOST(" + fmt(pierceX) + "," + fmt(pierceY) + "," + xyzFormat.format(ang) + "," + subId + "," + tech + ",0,0,0)");
  ostLines.push("HKPPP");

  cur = {
    subId: subId,
    pierceX: pierceX,
    pierceY: pierceY,
    approachCaptured: false,
    approachX: pierceX,
    approachY: pierceY,
    motions: [],
    cutPoints: [],
    firstCutSeen: false,
    forceType: (getSectionOverride("hkForceType", "AUTO") || "AUTO")
  };
}

// Motion functions
function capturePoint(x, y) {
  cur.cutPoints.push({ x: x, y: y });
}

function emitMotionLinear(x, y) {
  cur.motions.push("G1 " + xOutput.format(x) + " " + yOutput.format(y));
  capturePoint(x, y);
}

function emitMotionArc(clockwise, x, y, i, j) {
  cur.motions.push((clockwise ? "G2" : "G3") + " " + xOutput.format(x) + " " + yOutput.format(y) + " " + iOutput.format(i) + " " + jOutput.format(j));
  capturePoint(x, y);
}

function onLinear(x, y, z, feed) {
  if (!cur) return;
  var X = (x !== undefined) ? toOutUnits(x) : undefined;
  var Y = (y !== undefined) ? toOutUnits(y) : undefined;
  var mv = movement;

  if (!cur.approachCaptured) {
    if (mv === MOVEMENT_LEAD_IN || mv === MOVEMENT_PIERCE || mv === MOVEMENT_PIERCE_LINEAR || mv === MOVEMENT_PIERCE_PROFILE || mv === MOVEMENT_LINK_DIRECT || mv === MOVEMENT_LINK_TRANSITION) {
      if (X !== undefined && Y !== undefined) {
        cur.approachX = X;
        cur.approachY = Y;
        cur.approachCaptured = true;
      }
      return;
    }
    if (X !== undefined && Y !== undefined) {
      cur.approachCaptured = false;
      emitMotionLinear(X, Y);
      cur.firstCutSeen = true;
      return;
    }
  }
  if (X !== undefined && Y !== undefined) {
    emitMotionLinear(X, Y);
    cur.firstCutSeen = true;
  }
}

function onCircular(clockwise, cx, cy, cz, x, y, z, feed) {
  if (!cur) return;
  var X = (x !== undefined) ? toOutUnits(x) : undefined;
  var Y = (y !== undefined) ? toOutUnits(y) : undefined;
  var mv = movement;
  if (!cur.approachCaptured && (mv === MOVEMENT_LEAD_IN || mv === MOVEMENT_PIERCE || mv === MOVEMENT_PIERCE_CIRCULAR || mv === MOVEMENT_LINK_DIRECT || mv === MOVEMENT_LINK_TRANSITION)) {
    if (X !== undefined && Y !== undefined) {
      cur.approachX = X;
      cur.approachY = Y;
      cur.approachCaptured = true;
    }
    return;
  }
  if (X === undefined || Y === undefined) return;
  var start = getCurrentPosition();
  var sx = toOutUnits(start.x);
  var sy = toOutUnits(start.y);
  var I = toOutUnits(cx) - sx;
  var J = toOutUnits(cy) - sy;
  emitMotionArc(clockwise, X, Y, I, J);
  cur.firstCutSeen = true;
}

function onSectionEnd() {
  if (!cur) return;

  var leadX = 0, leadY = 0;
  var hasApproach = cur.approachCaptured && 
                    (Math.abs(cur.approachX - cur.pierceX) > 1e-6 || Math.abs(cur.approachY - cur.pierceY) > 1e-6);
  if (hasApproach) {
    leadX = cur.approachX - cur.pierceX;
    leadY = cur.approachY - cur.pierceY;
  }

  var pts = [];
  pts.push({ x: cur.pierceX, y: cur.pierceY });
  if (hasApproach) pts.push({ x: cur.approachX, y: cur.approachY });
  for (var i = 0; i < cur.cutPoints.length; i++) pts.push(cur.cutPoints[i]);

  var typeFlag = resolveHKTypeFlag(pts, cur.forceType);

  var out = [];
  out.push("N" + cur.subId + " HKSTR(" + typeFlag + ",1," + fmt(cur.pierceX) + "," + fmt(cur.pierceY) + ",0," + fmt(leadX) + "," + fmt(leadY) + ",0)");
  out.push("HKPIE(0,0,0)");
  out.push("HKLEA(0,0,0)");

  if (hasApproach) {
    out.push("G1 " + xOutput.format(cur.approachX) + " " + yOutput.format(cur.approachY));
    out.push("HKCUT(0,0,0)");
    for (var m = 0; m < cur.motions.length; m++) out.push(cur.motions[m]);
  } else {
    out.push("HKCUT(0,0,0)");
    for (var n = 0; n < cur.motions.length; n++) out.push(cur.motions[n]);
  }

  var whenLine = "WHEN ($AC_TIME>0.005)AND($R71<$R72)AND($R3==1)AND($R60==1) DO $A_DBB[10]=1";
  if (cur.motions.length > 0) {
    var whenPlacement = properties.whenPlacement;
    if (typeof whenPlacement === "string") whenPlacement = whenPlacement.toLowerCase();
    else whenPlacement = "beforelastmove";

    if (whenPlacement === "end") {
      out.push(whenLine);
    } else {
      var lastMotionIdx = -1;
      for (var k = out.length - 1; k >= 0; k--) {
        if (out[k].indexOf("G1 ") === 0 || out[k].indexOf("G2 ") === 0 || out[k].indexOf("G3 ") === 0) {
          lastMotionIdx = k;
          break;
        }
      }
      if (lastMotionIdx > 0) {
        out.splice(lastMotionIdx, 0, whenLine);
      } else {
        out.push(whenLine);
      }
    }
  }

  out.push("HKSTO(0,0,0)");
  blocks.push(out);
  cur = null;
}

function onClose() {

  // --------------------------------------------------------------------------
  // HK HEADER (always first)
  // --------------------------------------------------------------------------

  // Use sequence numbers starting from N1
  writeBlock("N1");
  // Use .value to extract actual numeric and string values from properties
  writeBlock(
    'HKLDB(' +
    properties.hkLdbA.value + ',"' +
    properties.hkLdbStr.value + '",' +
    properties.hkLdbB.value +
    ',0,0,0)'
  );

  writeBlock(
    "HKINI(" +
    properties.hkIniA.value + "," +
    properties.hkIniX.value + "," +
    properties.hkIniY.value +
    ",0,0,0)"
  );

  // --------------------------------------------------------------------------
  // FIRST SECTION START
  // --------------------------------------------------------------------------

  // First HKOST already collected during section processing
  for (var i = 0; i < ostLines.length; i++) {
    writeBlock(ostLines[i]);
  }


  // --------------------------------------------------------------------------
  // TOOLPATH BLOCKS
  // --------------------------------------------------------------------------

  for (var b = 0; b < blocks.length; b++) {
    var lines = blocks[b];
    for (var l = 0; l < lines.length; l++) {
      writeBlock(lines[l]);
    }
  }

  // --------------------------------------------------------------------------
  // WHEN CONDITION (ISOLATED, SAFE FOR 840D)
  // --------------------------------------------------------------------------

  if (properties.emitWhenCondition) {
    writeBlock(
      "N800000 WHEN ($AC_TIME>0.005) AND ($R71<$R72) AND ($R3==1) AND ($R60==1) DO $A_DBB[10]=1"
    );
  }

  // --------------------------------------------------------------------------
  // HK FOOTER (MUST BE LAST)
  // --------------------------------------------------------------------------

  writeBlock("HKSTO(0,0,0)");

  if (properties.emitHKPEDAtEnd) {
    writeBlock("HKPED(0,0,0)");
  }

  // Compute end block number as in original post pattern
  var endN = (sectionCount + 1) * 10000 * 10;
  writeBlock("N" + endN + " HKEND(0,0,0)");
  // Use a lower block number for M30, matching original output
  writeBlock("N10 M30");
}

