import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { SpecBuilder } from "@/components/plan-editor/spec-builder";
import { toast } from "sonner";

vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() },
}));

const submitButtonName = /Generate 4 candidates/;

describe("SpecBuilder", () => {
  beforeEach(() => {
    vi.mocked(toast.error).mockClear();
  });

  it("renders with the 3 default rooms pre-filled", () => {
    render(<SpecBuilder onSubmit={vi.fn()} />);
    const names = screen.getAllByLabelText("Room name").map((el) => (el as HTMLInputElement).value);
    expect(names).toEqual(["Bedroom 1", "Kitchen", "Living"]);
  });

  it("submits a valid spec built from the default rooms", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<SpecBuilder onSubmit={onSubmit} />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: submitButtonName }));
    });

    expect(onSubmit).toHaveBeenCalledTimes(1);
    const spec = onSubmit.mock.calls[0][0];
    expect(spec.spec_id).toMatch(/^spec_[a-z0-9_]+$/);
    expect(spec.room_requirements).toHaveLength(3);
    expect(spec.n_candidates).toBe(4);
    expect(spec.original_nl).toBe("Generate a plan matching the above room requirements.");
    const kitchen = spec.room_requirements.find((r: { name: string }) => r.name === "Kitchen");
    expect(kitchen.adjacencies).toEqual(["Living"]);
  });

  it("rejects duplicate room names (case-insensitive) without calling onSubmit", () => {
    const onSubmit = vi.fn();
    render(<SpecBuilder onSubmit={onSubmit} />);
    const nameInputs = screen.getAllByLabelText("Room name");
    fireEvent.change(nameInputs[2], { target: { value: "kitchen" } });
    fireEvent.click(screen.getByRole("button", { name: submitButtonName }));

    expect(toast.error).toHaveBeenCalled();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("silently filters adjacencies that don't match another room", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<SpecBuilder onSubmit={onSubmit} />);
    const adjacencyInputs = screen.getAllByLabelText("Adjacent to (comma-separated room names)");
    fireEvent.change(adjacencyInputs[0], { target: { value: "Living, Nonexistent Room" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: submitButtonName }));
    });

    const spec = onSubmit.mock.calls[0][0];
    const bedroom = spec.room_requirements.find((r: { name: string }) => r.name === "Bedroom 1");
    expect(bedroom.adjacencies).toEqual(["Living"]);
  });

  it("disables the submit button when all rooms are removed", () => {
    render(<SpecBuilder onSubmit={vi.fn()} />);
    while (screen.queryAllByLabelText("Remove room").length > 0) {
      fireEvent.click(screen.getAllByLabelText("Remove room")[0]);
    }
    expect(screen.getByRole("button", { name: submitButtonName })).toBeDisabled();
  });
});
