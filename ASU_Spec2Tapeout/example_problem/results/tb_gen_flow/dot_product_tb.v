`timescale 1ns/1ps

module dot_product_tb;

    parameter N = 8;
    parameter WIDTH = 8;
    parameter PERIOD = 4.5;

    reg clk;
    reg rst;
    reg signed [N*WIDTH-1:0] A;
    reg signed [N*WIDTH-1:0] B;
    wire signed [2*WIDTH+3:0] dot_out;

    dot_product #(
        .N(N),
        .WIDTH(WIDTH)
    ) dut (
        .clk(clk),
        .rst(rst),
        .A(A),
        .B(B),
        .dot_out(dot_out)
    );

    function signed [2*WIDTH+3:0] compute_dot;
        input signed [N*WIDTH-1:0] va;
        input signed [N*WIDTH-1:0] vb;
        integer i;
        reg signed [WIDTH-1:0] a_el, b_el;
        reg signed [2*WIDTH-1:0] prod;
        reg signed [2*WIDTH+3:0] sum;
        begin
            sum = 0;
            for (i = 0; i < N; i = i + 1) begin
                a_el = va[i*WIDTH +: WIDTH];
                b_el = vb[i*WIDTH +: WIDTH];
                prod = a_el * b_el;
                sum = sum + prod;
            end
            compute_dot = sum;
        end
    endfunction

    reg signed [2*WIDTH+3:0] exp_pipe [0:1];
    reg signed [N*WIDTH-1:0] a_pipe [0:1];
    reg signed [N*WIDTH-1:0] b_pipe [0:1];
    reg v_pipe [0:1];

    always @(posedge clk) begin
        if (rst) begin
            v_pipe[0] <= 1'b0;
            v_pipe[1] <= 1'b0;
            exp_pipe[0] <= 0;
            exp_pipe[1] <= 0;
            a_pipe[0] <= 0;
            a_pipe[1] <= 0;
            b_pipe[0] <= 0;
            b_pipe[1] <= 0;
        end else begin
            v_pipe[0]   <= 1'b1;
            exp_pipe[0] <= compute_dot(A, B);
            a_pipe[0]   <= A;
            b_pipe[0]   <= B;

            v_pipe[1]   <= v_pipe[0];
            exp_pipe[1] <= exp_pipe[0];
            a_pipe[1]   <= a_pipe[0];
            b_pipe[1]   <= b_pipe[0];

            if (v_pipe[1]) begin
                if (dot_out === exp_pipe[1]) begin
                    $display("TEST: PASS | Inputs: A=%h, B=%h | Expected: %d | Output: %d", a_pipe[1], b_pipe[1], exp_pipe[1], dot_out);
                end else begin
                    $display("TEST: FAIL | Inputs: A=%h, B=%h | Expected: %d | Output: %d", a_pipe[1], b_pipe[1], exp_pipe[1], dot_out);
                end
            end
        end
    end

    initial begin
        clk = 0;
        forever #(PERIOD/2.0) clk = ~clk;
    end

    integer k;
    initial begin
        A = 0;
        B = 0;
        rst = 1;
        #(PERIOD * 2);
        
        @(negedge clk);
        rst = 0;

        A = 64'hE006090E1FCE32D8;
        B = 64'h1D1632250E291EFF;
        @(negedge clk);

        for (k = 0; k < 20; k = k + 1) begin
            A[31:0] = $random;
            A[63:32] = $random;
            B[31:0] = $random;
            B[63:32] = $random;
            @(negedge clk);
        end

        A = 0;
        B = 0;
        repeat (5) @(negedge clk);

        $finish;
    end

endmodule