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
      <CandidateGallery response={null} onGenerateAgain={vi.fn()} onUseCandidate={vi.fn()} />
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders one card per layout, sorted by rank, with score formatting", () => {
    render(<CandidateGallery response={baseResponse} onGenerateAgain={vi.fn()} onUseCandidate={vi.fn()} />);

    expect(screen.getByText("Candidate #1")).toBeInTheDocument();
    expect(screen.getByText("Candidate #2")).toBeInTheDocument();
    expect(screen.getByText("87 / 100")).toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("shows a cached badge when cached is true", () => {
    render(<CandidateGallery response={baseResponse} onGenerateAgain={vi.fn()} onUseCandidate={vi.fn()} />);
    expect(screen.getByText("Cached")).toBeInTheDocument();
  });

  it("does not show a cached badge when cached is false", () => {
    render(
      <CandidateGallery
        response={{ ...baseResponse, cached: false }}
        onGenerateAgain={vi.fn()}
        onUseCandidate={vi.fn()}
      />
    );
    expect(screen.queryByText("Cached")).not.toBeInTheDocument();
  });

  it("disables 'Use this candidate' with the coming-soon tooltip", () => {
    render(<CandidateGallery response={baseResponse} onGenerateAgain={vi.fn()} onUseCandidate={vi.fn()} />);
    const buttons = screen.getAllByRole("button", { name: "Use this candidate" });
    expect(buttons).toHaveLength(2);
    for (const button of buttons) {
      expect(button).toBeDisabled();
      expect(button).toHaveAttribute("title", "Preview render coming in the next release");
    }
  });

  it("calls onGenerateAgain when Regenerate is clicked", () => {
    const onGenerateAgain = vi.fn();
    render(<CandidateGallery response={baseResponse} onGenerateAgain={onGenerateAgain} onUseCandidate={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Regenerate" }));
    expect(onGenerateAgain).toHaveBeenCalledTimes(1);
  });
});
