import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { LayoutPreview } from "@/components/plan-editor/layout-preview";
import type { Layout } from "@/lib/api";

const layout: Layout = {
  plan_id: "plan_test1",
  schema_version: "1.0",
  rooms: [
    {
      id: "room_a",
      name: "Living Room",
      room_type: "living",
      vertices: [
        [0, 0],
        [10, 0],
        [10, 10],
        [0, 10],
        [0, 0],
      ],
      area_sqft: 100,
      boundary_wall_ids: ["wall_a_s", "wall_mid", "wall_a_n", "wall_a_w"],
    },
    {
      id: "room_b",
      name: "Closet",
      room_type: "closet",
      vertices: [
        [10, 0],
        [20, 0],
        [20, 10],
        [10, 10],
        [10, 0],
      ],
      area_sqft: 100,
      boundary_wall_ids: ["wall_b_s", "wall_b_e", "wall_b_n", "wall_mid"],
    },
  ],
  walls: [
    { id: "wall_a_s", start: [0, 0], end: [10, 0], bounds_rooms: ["room_a"] },
    { id: "wall_mid", start: [10, 0], end: [10, 10], bounds_rooms: ["room_a", "room_b"], thickness_ft: 0.5 },
  ],
  openings: [
    { id: "opening_1", opening_type: "door", wall_id: "wall_mid", offset_ft: 4, width_ft: 3 },
  ],
  extent_x_ft: 20,
  extent_y_ft: 10,
};

describe("LayoutPreview", () => {
  it("renders an svg with a viewBox matching the layout extent", () => {
    const { container } = render(<LayoutPreview layout={layout} />);
    const svg = container.querySelector("svg");
    expect(svg).toHaveAttribute("viewBox", "0 0 20 10");
  });

  it("renders one polygon per room and one line per wall", () => {
    const { container } = render(<LayoutPreview layout={layout} />);
    expect(container.querySelectorAll("polygon")).toHaveLength(2);
    // 2 walls + 1 opening cutout line = 3 <line> elements
    expect(container.querySelectorAll("line")).toHaveLength(3);
  });

  it("renders room name labels by default", () => {
    render(<LayoutPreview layout={layout} />);
    expect(screen.getByText("Living Room")).toBeInTheDocument();
    expect(screen.getByText("Closet")).toBeInTheDocument();
  });

  it("omits labels when showRoomLabels is false", () => {
    render(<LayoutPreview layout={layout} showRoomLabels={false} />);
    expect(screen.queryByText("Living Room")).not.toBeInTheDocument();
  });

  it("skips an opening whose wall_id doesn't resolve instead of crashing", () => {
    const brokenLayout: Layout = {
      ...layout,
      openings: [{ id: "opening_x", opening_type: "door", wall_id: "does_not_exist", offset_ft: 0, width_ft: 1 }],
    };
    const { container } = render(<LayoutPreview layout={brokenLayout} />);
    expect(container.querySelectorAll("line")).toHaveLength(2); // just the 2 walls
  });
});
