`timescale 1ns/1ps

module tb_fp16_multiplier;
    reg [15:0] a;
    reg [15:0] b;
    wire [15:0] result;
    reg clk;
    reg reset;

    fp16_multiplier dut (
        .a(a),
        .b(b),
        .result(result)
    );

    initial begin
        clk = 0;
        forever #4.5 clk = ~clk;
    end

    task drive_and_check;
        input [15:0] t_a;
        input [15:0] t_b;
        input [15:0] t_exp;
        integer timeout;
        begin
            @(negedge clk);
            a = t_a;
            b = t_b;
            timeout = 0;
            while (timeout < 1) begin
                @(posedge clk);
                timeout = timeout + 1;
            end
            #1;
            if (result === t_exp) begin
                $display("TEST: PASS | Inputs: a=%h b=%h | Expected: %h | Output: %h", t_a, t_b, t_exp, result);
            end else begin
                $display("TEST: FAIL | Inputs: a=%h b=%h | Expected: %h | Output: %h", t_a, t_b, t_exp, result);
            end
        end
    endtask

    initial begin
        a = 16'h0000;
        b = 16'h0000;
        reset = 1;
        #18;
        reset = 0;

        // Basic normal multiplication
        drive_and_check(16'h3c00, 16'h4000, 16'h4000); // 1.0 * 2.0 = 2.0
        drive_and_check(16'h3e00, 16'h3e00, 16'h4080); // 1.5 * 1.5 = 2.25
        drive_and_check(16'hbc00, 16'h4000, 16'hc000); // -1.0 * 2.0 = -2.0

        // Zero detection
        drive_and_check(16'h0000, 16'h4000, 16'h0000); // 0.0 * 2.0 = 0.0
        drive_and_check(16'h8000, 16'h3c00, 16'h8000); // -0.0 * 1.0 = -0.0
        drive_and_check(16'h0000, 16'h0000, 16'h0000); // 0.0 * 0.0 = 0.0

        // Special IEEE 754 values
        drive_and_check(16'h7c00, 16'h3c00, 16'h7c00); // Inf * 1.0 = Inf
        drive_and_check(16'h7e00, 16'h3c00, 16'h7e00); // NaN * 1.0 = NaN
        drive_and_check(16'h7c00, 16'h0000, 16'h7e00); // Inf * 0.0 = NaN (Standard behavior)

        // Overflow and Underflow
        drive_and_check(16'h7bff, 16'h4000, 16'h7c00); // Max * 2.0 = Inf (Overflow)
        drive_and_check(16'h0001, 16'h0001, 16'h0000); // Smallest sub * Smallest sub = 0 (Underflow)

        // Rounding to Nearest Even (RNE) Halfway Cases
        // Case 1: 1.5 * 1.0009765625 = 1.50146484375 (Halfway, round up to 1.501953125 / 0x3e02)
        drive_and_check(16'h3e00, 16'h3c01, 16'h3e02); 
        
        // Case 2: 1.5 * 1.0029296875 = 1.50439453125 (Halfway, round down to 1.50390625 / 0x3e04)
        drive_and_check(16'h3e00, 16'h3c03, 16'h3e04);

        // Subnormals
        drive_and_check(16'h0001, 16'h3c00, 16'h0001); // Subnormal * 1.0 = Subnormal
        drive_and_check(16'h0001, 16'h4000, 16'h0002); // Subnormal * 2.0 = Subnormal

        #18;
        $finish;
    end

endmodule