import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { CandidateGallery } from "@/components/plan-editor/candidate-gallery";
import type { GenerateResponse } from "@/lib/api";

const baseResponse: GenerateResponse = {
  generated_plan_id: "gp1",
  spec_hash: "hash",
  generator_name: "stub",
  generator_version: "2026-07-14",
  total_candidates: 2,
  survived_layer_a: 2,
  survived_layer_c: 2,
  elapsed_ms: 11,
  cached: true,
  layouts: [
    { selection_rank: 0, user_score: 0.87, plan_id: "plan-aaaa-1111" },
    { selection_rank: 1, user_score: null, plan_id: "plan-bbbb-2222" },
  ],
};

describe("CandidateGallery", () => {
  it("renders nothing when response is null", () => {
    const { container } = render(
      <CandidateGallery
        response={null}
        onGenerateAgain={vi.fn()}
        onUseCandidate={vi.fn()}
        materializingRank={null}
      />
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders one card per layout, sorted by rank, with score formatting", () => {
    render(
      <CandidateGallery
        response={baseResponse}
        onGenerateAgain={vi.fn()}
        onUseCandidate={vi.fn()}
        materializingRank={null}
      />
    );

    expect(screen.getByText("Candidate #1")).toBeInTheDocument();
    expect(screen.getByText("Candidate #2")).toBeInTheDocument();
    expect(screen.getByText("87 / 100")).toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("shows a cached badge when cached is true", () => {
    render(
      <CandidateGallery
        response={baseResponse}
        onGenerateAgain={vi.fn()}
        onUseCandidate={vi.fn()}
        materializingRank={null}
      />
    );
    expect(screen.getByText("Cached")).toBeInTheDocument();
  });

  it("does not show a cached badge when cached is false", () => {
    render(
      <CandidateGallery
        response={{ ...baseResponse, cached: false }}
        onGenerateAgain={vi.fn()}
        onUseCandidate={vi.fn()}
        materializingRank={null}
      />
    );
    expect(screen.queryByText("Cached")).not.toBeInTheDocument();
  });

  it("falls back to the placeholder preview when layouts_full is absent", () => {
    render(
      <CandidateGallery
        response={baseResponse}
        onGenerateAgain={vi.fn()}
        onUseCandidate={vi.fn()}
        materializingRank={null}
      />
    );
    expect(screen.getAllByText("Preview render coming soon")).toHaveLength(2);
  });

  it("renders a LayoutPreview per candidate when layouts_full is present", () => {
    const layout = {
      plan_id: "plan_test1",
      schema_version: "1.0",
      rooms: [
        {
          id: "room_1",
          name: "Living",
          room_type: "living",
          vertices: [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]] as [number, number][],
          area_sqft: 100,
          boundary_wall_ids: ["wall_1"],
        },
      ],
      walls: [],
      openings: [],
      extent_x_ft: 10,
      extent_y_ft: 10,
    };
    render(
      <CandidateGallery
        response={{
          ...baseResponse,
          layouts_full: [
            { selection_rank: 0, user_score: 0.87, layout },
            { selection_rank: 1, user_score: null, layout },
          ],
        }}
        onGenerateAgain={vi.fn()}
        onUseCandidate={vi.fn()}
        materializingRank={null}
      />
    );
    expect(screen.queryByText("Preview render coming soon")).not.toBeInTheDocument();
    expect(screen.getAllByLabelText("Floor plan preview")).toHaveLength(2);
  });

  it("enables 'Use this candidate' and calls onUseCandidate when clicked", () => {
    const onUseCandidate = vi.fn().mockResolvedValue(undefined);
    render(
      <CandidateGallery
        response={baseResponse}
        onGenerateAgain={vi.fn()}
        onUseCandidate={onUseCandidate}
        materializingRank={null}
      />
    );
    const buttons = screen.getAllByRole("button", { name: "Use this candidate" });
    expect(buttons).toHaveLength(2);
    for (const button of buttons) expect(button).not.toBeDisabled();

    fireEvent.click(buttons[0]);
    expect(onUseCandidate).toHaveBeenCalledWith(baseResponse.layouts[0]);
  });

  it("disables all buttons and shows a loading state while materializing", () => {
    render(
      <CandidateGallery
        response={baseResponse}
        onGenerateAgain={vi.fn()}
        onUseCandidate={vi.fn()}
        materializingRank={0}
      />
    );
    const buttons = screen.getAllByRole("button", { name: /Use this candidate|Opening/ });
    expect(buttons).toHaveLength(2);
    for (const button of buttons) expect(button).toBeDisabled();
    expect(screen.getByText("Opening…")).toBeInTheDocument();
  });

  it("calls onGenerateAgain when Regenerate is clicked", () => {
    const onGenerateAgain = vi.fn();
    render(
      <CandidateGallery
        response={baseResponse}
        onGenerateAgain={onGenerateAgain}
        onUseCandidate={vi.fn()}
        materializingRank={null}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: "Regenerate" }));
    expect(onGenerateAgain).toHaveBeenCalledTimes(1);
  });
});
