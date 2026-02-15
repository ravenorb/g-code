// Fusion 360 HK Macro Post (fresh template)
// This file is a clean-room, C++-style reference implementation based on
// HK macro sequencing rules and the schema captured in samples/schema.txt.
//
// Expected macro order per operation:
//   HKOST -> HKSTR -> HKPIE -> HKLEA -> HKCUT -> HKSTO -> HKPED
//
// Program envelope:
//   HKLDB -> HKINI -> (operations...) -> HKEND -> M30
//
// Notes:
// - HKOST must appear before HKSTR.
// - HKCUT must precede the first cutting G1 move.
// - HKSTO must precede HKPED.
// - Operation IDs must match N-label blocks.

#include <cmath>
#include <iomanip>
#include <iostream>
#include <optional>
#include <sstream>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

namespace hk {

struct Point {
  double x{0.0};
  double y{0.0};
  double z{0.0};
};

struct Motion {
  std::string cmd;
  std::optional<double> x;
  std::optional<double> y;
  std::optional<double> i;
  std::optional<double> j;
};

enum class KerfMode { kNone = 0, kCompensated = 1 };

struct CutSequence {
  std::string type{"contour"};
  KerfMode kerf{KerfMode::kCompensated};
  Point start{};
  Point lead_target{};
  std::vector<Motion> motion{};
};

struct Operation {
  int operation_id{0};
  int technology{0};
  Point anchor{};
  CutSequence cut{};
};

struct ProgramConfig {
  int material_library{2};
  std::string material_name{"S304"};
  int process_class{3};
  int init_mode{15};
  double sheet_x{118.3};
  double sheet_y{13.9};
  double sheet_thickness_mm{1.5};
};

using TechMap = std::unordered_map<std::string, int>;
using ThicknessMap = std::unordered_map<std::string, TechMap>;
using MaterialMap = std::unordered_map<std::string, ThicknessMap>;

class HkPostProcessor {
 public:
  explicit HkPostProcessor(ProgramConfig config)
      : config_(std::move(config)) {}

  void set_technology_map(MaterialMap map) { technology_map_ = std::move(map); }

  void begin_program(std::ostream& out) {
    out << "HKLDB(" << config_.material_library << ",\""
        << config_.material_name << "\"," << config_.process_class << ")\n";
    out << "HKINI(" << config_.init_mode << "," << fmt(config_.sheet_x) << ","
        << fmt(config_.sheet_y) << ")\n";
  }

  void register_operation(std::ostream& out, Operation& op) {
    if (op.technology <= 0) {
      op.technology = resolve_tech(op.cut.type);
    }
    out << "N" << op.operation_id << " ";
    out << "HKOST(" << fmt(op.anchor.x) << "," << fmt(op.anchor.y) << ","
        << fmt(op.anchor.z) << "," << op.operation_id << ","
        << op.technology << ",0)\n";
    out << "HKPPP\n";
  }

  void begin_section(std::ostream& out, const Operation& op) {
    out << "HKSTR(" << fmt(op.cut.start.x) << "," << fmt(op.cut.start.y) << ","
        << fmt(op.cut.start.z) << "," << fmt(op.cut.lead_target.x) << ","
        << fmt(op.cut.lead_target.y) << "," << fmt(op.cut.lead_target.z)
        << ")\n";
    out << "HKPIE(0,0,0)\n";
    out << "HKLEA(0,0,0)\n";
  }

  void emit_first_cut_move(std::ostream& out) { out << "HKCUT(0,0,0)\n"; }

  void emit_motion(std::ostream& out, const Motion& motion) {
    std::ostringstream line;
    line << motion.cmd;
    append_axis(line, 'X', motion.x);
    append_axis(line, 'Y', motion.y);
    append_axis(line, 'I', motion.i);
    append_axis(line, 'J', motion.j);
    out << line.str() << "\n";
  }

  void end_section(std::ostream& out) {
    out << "HKSTO(0,0,0)\n";
    out << "HKPED(0,0,0)\n";
  }

  void end_program(std::ostream& out) {
    out << "HKEND(0,0,0)\n";
    out << "M30\n";
  }

 private:
  ProgramConfig config_{};
  MaterialMap technology_map_{};

  int resolve_tech(const std::string& op_type) const {
    const auto& material = config_.material_name;
    const auto thickness_key = thickness_key_from_mm(config_.sheet_thickness_mm);

    auto material_it = technology_map_.find(material);
    if (material_it == technology_map_.end()) return 0;

    const auto& thickness_map = material_it->second;
    auto thickness_it = thickness_map.find(thickness_key);
    if (thickness_it == thickness_map.end()) {
      thickness_it = thickness_map.find("default");
      if (thickness_it == thickness_map.end()) return 0;
    }

    const auto& tech_map = thickness_it->second;
    auto tech_it = tech_map.find(op_type);
    if (tech_it == tech_map.end()) return 0;
    return tech_it->second;
  }

  static std::string thickness_key_from_mm(double thickness_mm) {
    if (thickness_mm <= 0) return "default";
    double rounded = std::round(thickness_mm * 10.0) / 10.0;
    std::ostringstream key;
    key << std::fixed << std::setprecision(1) << rounded << "mm";
    return key.str();
  }

  static std::string fmt(double value) {
    std::ostringstream ss;
    ss << std::fixed << std::setprecision(4) << value;
    return ss.str();
  }

  static void append_axis(std::ostringstream& line, char axis,
                          const std::optional<double>& value) {
    if (!value.has_value()) return;
    line << " " << axis << fmt(*value);
  }
};

}  // namespace hk

// Usage example (standalone integration test)
int main() {
  hk::ProgramConfig config{};
  hk::HkPostProcessor post(config);

  hk::MaterialMap tech_map{
      {"S304", {{"1.5mm", {{"contour", 5}, {"slot", 3}, {"pierce-only", 9}}},
                {"default", {{"contour", 5}, {"slot", 3}, {"pierce-only", 9}}}}}};
  post.set_technology_map(tech_map);

  hk::Operation op{};
  op.operation_id = 10001;
  op.anchor = {0.3, 6.8, 0.0};
  op.cut.type = "contour";
  op.cut.start = {28.6017, 3.5914, 0.0};
  op.cut.lead_target = {28.9375, 3.5886, 0.0};
  op.cut.motion.push_back({"G1", 28.6903, 3.5028, std::nullopt, std::nullopt});
  op.cut.motion.push_back({"G1", 28.9415, 3.2516, std::nullopt, std::nullopt});

  post.begin_program(std::cout);
  post.register_operation(std::cout, op);
  post.begin_section(std::cout, op);
  post.emit_first_cut_move(std::cout);
  for (const auto& move : op.cut.motion) {
    post.emit_motion(std::cout, move);
  }
  post.end_section(std::cout);
  post.end_program(std::cout);

  return 0;
}
