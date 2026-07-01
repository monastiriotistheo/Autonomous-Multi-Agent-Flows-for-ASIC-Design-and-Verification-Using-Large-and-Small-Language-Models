`timescale 1ns/1ps

module dot_product #(
    parameter N = 8,
    parameter WIDTH = 8
) (
    input                           clk,
    input                           rst,
    input      signed [N*WIDTH-1:0] A,
    input      signed [N*WIDTH-1:0] B,
    output reg signed [2*WIDTH+3:0] dot_out
);

    integer i;
    integer j;
    reg signed [N*2*WIDTH-1:0] products_flat;
    reg signed [2*WIDTH+3:0] sum_comb;

    // Stage 1: Pipelined Multipliers
    // Samples inputs A and B and performs signed element-wise multiplication
    always @(posedge clk) begin
        if (rst) begin
            products_flat <= 0;
        end else begin
            for (i = 0; i < N; i = i + 1) begin
                products_flat[i*2*WIDTH +: 2*WIDTH] <= $signed(A[i*WIDTH +: WIDTH]) * $signed(B[i*WIDTH +: WIDTH]);
            end
        end
    end

    // Stage 2 Combinatorial: Summation of partial products
    always @(*) begin
        sum_comb = 0;
        for (j = 0; j < N; j = j + 1) begin
            sum_comb = sum_comb + $signed(products_flat[j*2*WIDTH +: 2*WIDTH]);
        end
    end

    // Stage 2 Sequential: Output register to complete the 2nd pipeline stage
    always @(posedge clk) begin
        if (rst) begin
            dot_out <= 0;
        end else begin
            dot_out <= sum_comb;
        end
    end

endmodule