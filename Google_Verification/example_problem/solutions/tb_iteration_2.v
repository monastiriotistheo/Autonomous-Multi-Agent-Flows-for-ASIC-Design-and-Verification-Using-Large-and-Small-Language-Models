`timescale 1ns/1ps

module testbench;

  // Declarations
  reg [9:0] bin;
  wire [9:0] gray;
  reg [9:0] expected_gray;

  // Instantiate the DUT
  enc_bin2gray dut (.bin(bin), .gray(gray));

  // Testbench logic
  initial begin
    // Initialize signals
    bin = 10'b0;

    // Loop through all possible input values
    for (integer i = 0; i < 2**10; i = i + 1) begin
      bin = i;
      #10; // Delay to allow signal propagation

      // Calculate the expected Gray code
      expected_gray = bin ^ (bin >> 1);

      // Check if the output matches the expected Gray code
      if (gray !== expected_gray) begin
        $display("TEST FAILED");
        $finish;
      end
    end

    // All tests passed
    $display("TEST PASSED");
    $finish;
  end

endmodule