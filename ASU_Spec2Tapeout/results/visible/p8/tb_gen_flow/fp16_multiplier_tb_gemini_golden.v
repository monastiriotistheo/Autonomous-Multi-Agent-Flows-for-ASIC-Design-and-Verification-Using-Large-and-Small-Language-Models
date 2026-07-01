`timescale 1ns/1ps

module fp16_multiplier_tb;

    reg [15:0] a;
    reg [15:0] b;
    wire [15:0] result;

    reg clk;
    reg rst_n;

    fp16_multiplier dut (
        .a(a),
        .b(b),
        .result(result)
    );

    initial begin
        clk = 0;
        forever #4.5 clk = ~clk;
    end

    initial begin
        rst_n = 0;
        a = 16'h0000;
        b = 16'h0000;
        #1;
        rst_n = 1;

        // TC 1: Standard Usage (1.0 * 2.0 = 2.0)
        check_fp16(16'h3c00, 16'h4000, 16'h4000);

        // TC 2: Zero Detection (0.0 * 5.0 = 0.0)
        check_fp16(16'h0000, 16'h4500, 16'h0000);

        // TC 3: Signed Zero (-0.0 * 1.0 = -0.0)
        check_fp16(16'h8000, 16'h3c00, 16'h8000);

        // TC 4: Normal Multiply (1.5 * 1.5 = 2.25)
        check_fp16(16'h3e00, 16'h3e00, 16'h4080);

        // TC 5: Negative Multiplication (-2.0 * 1.5 = -3.0)
        check_fp16(16'hc000, 16'h3e00, 16'hc200);

        // TC 6: Infinity (Inf * 1.0 = Inf)
        check_fp16(16'h7c00, 16'h3c00, 16'h7c00);

        // TC 7: NaN (NaN * 1.0 = NaN)
        check_fp16(16'h7e00, 16'h3c00, 16'h7e00);

        // TC 8: Invalid Operation (0 * Inf = NaN)
        check_fp16(16'h0000, 16'h7c00, 16'h7e00);

        // TC 9: Overflow to Infinity (Max Normal * 2.0 = Inf)
        check_fp16(16'h7bff, 16'h4000, 16'h7c00);

        // TC 10: Rounding to Nearest Even (Tie-Down Case)
        // 1.015625 (0x3c10) * 1.03125 (0x3c20) = 1.04736328125
        // Significand: 1.0000110000 | 1 (Tie). LSB is 0 (even), so stay 0.
        check_fp16(16'h3c10, 16'h3c20, 16'h3c30);

        // TC 11: Rounding to Nearest Even (Tie-Up Case)
        // 1.0009765625 (0x3c01) * 1.5 (0x3e00) = 1.50146484375
        // Significand: 1.1000000001 | 1 (Tie). LSB is 1 (odd), so round up to 1.1000000010.
        check_fp16(16'h3c01, 16'h3e00, 16'h3e02);

        // TC 12: Subnormal Operations (Smallest Subnormal * 2.0)
        check_fp16(16'h0001, 16'h4000, 16'h0002);

        // TC 13: Underflow (Subnormal * 0.5 = 0.0)
        check_fp16(16'h0001, 16'h3800, 16'h0000);

        $finish;
    end

    task check_fp16;
        input [15:0] in_a;
        input [15:0] in_b;
        input [15:0] expected;
        begin
            @(negedge clk);
            a = in_a;
            b = in_b;
            @(posedge clk);
            #1;
            if (result === expected) begin
                $display("TEST: PASS | Inputs: a=%h, b=%h | Expected: %h | Output: %h", in_a, in_b, expected, result);
            end else begin
                $display("TEST: FAIL | Inputs: a=%h, b=%h | Expected: %h | Output: %h", in_a, in_b, expected, result);
            end
        end
    endtask

endmodule