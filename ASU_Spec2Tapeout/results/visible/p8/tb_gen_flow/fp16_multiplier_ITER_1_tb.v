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
        begin
            @(negedge clk);
            a = t_a;
            b = t_b;
            @(posedge clk);
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
        #10;
        reset = 0;

        drive_and_check(16'h3c00, 16'h4000, 16'h4000);
        drive_and_check(16'h3c00, 16'h3c00, 16'h3c00);
        drive_and_check(16'h0000, 16'h4000, 16'h0000);
        drive_and_check(16'hbc00, 16'h4000, 16'hc000);
        drive_and_check(16'h4000, 16'h4200, 16'h4600);
        drive_and_check(16'h3e00, 16'h3e00, 16'h4080);
        drive_and_check(16'hc000, 16'hc000, 16'h4400);
        drive_and_check(16'h3800, 16'h3800, 16'h3400);
        drive_and_check(16'hbc00, 16'hbc00, 16'h3c00);
        drive_and_check(16'h0000, 16'h0000, 16'h0000);

        #10;
        $finish;
    end

endmodule